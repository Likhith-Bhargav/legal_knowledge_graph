import logging
import re
from pathlib import Path

import pdfplumber

from src.ingestion.parsers import DocumentParser, ParsedDocument
from src.models.domain import Act, Section

logger = logging.getLogger(__name__)

class RegexLegalParser(DocumentParser):
    """
    100% Rule-Based (Regex) parser for Legal PDFs.
    Bypasses costly/slow LLM processing.
    """

    def __init__(self, act_id: str, title: str, year: int, number: str, short_title: str | None = None) -> None:
        self.act_id = act_id
        self.title = title
        self.year = year
        self.number = number
        self.short_title = short_title

    def can_parse(self, source: str | Path | dict) -> bool:
        if isinstance(source, (str, Path)):
            p = Path(source)
            return p.suffix.lower() == ".pdf" and p.exists()
        return False

    def parse(self, source: str | Path | dict) -> ParsedDocument:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {source}")

        logger.info(f"Extracting raw text via strict Regex rules from: {path.name}")
        full_text = ""
        
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                logger.info(f"Scanning page {i+1}/{len(pdf.pages)} ...")
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
                    
        return self._extract_sections(full_text)

    def _extract_sections(self, text: str) -> ParsedDocument:
        from src.models.domain import Subsection, Clause
        lines = text.split("\n")
        
        act = Act(
            id=self.act_id,
            title=self.title,
            year=self.year,
            number=self.number,
            short_title=self.short_title,
            description="Ingested via Regex Fast-Pass"
        )
        
        sections = []
        current_section_data = None
        current_content = []
        
        # Section: "1. Short title..."
        section_pattern = re.compile(r"^\s*(\d+[a-zA-Z]?)\.\s+(.+)$")
        # Subsection: "(1)" or "(2)"
        subsection_pattern = re.compile(r"^\s*\((\d+[a-zA-Z]?)\)\s+(.*)$")
        # Clause: "(a)" or "(b)"
        clause_pattern = re.compile(r"^\s*\(([a-z])\)\s+(.*)$")
        
        last_section_num = 0

        def finalize_section(sec_data, content_lines):
            if not sec_data:
                return None
            
            full_text = "\n".join(content_lines)
            subsections = []
            
            # Sub-parsing the content_lines for subsections/clauses
            curr_sub = None
            for line in content_lines:
                sub_match = subsection_pattern.match(line.strip())
                if sub_match:
                    if curr_sub:
                        subsections.append(curr_sub)
                    sub_num = sub_match.group(1)
                    curr_sub = Subsection(
                        id=f"{sec_data['id']}_sub_{sub_num}",
                        number=sub_num,
                        content=sub_match.group(2).strip(),
                        section_id=sec_data['id'],
                        clauses=[]
                    )
                    continue
                
                clause_match = clause_pattern.match(line.strip())
                if clause_match and curr_sub:
                    cl_id = clause_match.group(1)
                    curr_sub.clauses.append(
                        Clause(
                            id=f"{curr_sub.id}_cl_{cl_id}",
                            identifier=cl_id,
                            content=clause_match.group(2).strip(),
                            section_id=sec_data['id'],
                            subsection_id=curr_sub.id
                        )
                    )
                    continue
                
                if curr_sub:
                    curr_sub = curr_sub.model_copy(update={"content": curr_sub.content + " " + line.strip()})

            if curr_sub:
                subsections.append(curr_sub)

            return Section(
                id=sec_data["id"],
                number=sec_data["number"],
                title=sec_data["title"],
                original_content=full_text,
                effective_content=full_text,
                act_id=self.act_id,
                order=len(sections),
                subsections=subsections
            )
        
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
                
            match = section_pattern.match(line_str)
            if match:
                sec_num_str = match.group(1)
                int_part = 0
                import string
                num_digits = sec_num_str.rstrip(string.ascii_letters)
                if num_digits.isdigit():
                    int_part = int(num_digits)
                
                if 0 <= int_part < last_section_num + 50 or last_section_num == 0:
                    new_sec = finalize_section(current_section_data, current_content)
                    if new_sec:
                        sections.append(new_sec)
                        
                    last_section_num = max(last_section_num, int_part)
                    current_section_data = {
                        "id": f"{self.act_id}_sec_{sec_num_str}",
                        "number": sec_num_str,
                        "title": match.group(2).strip(),
                    }
                    current_content = [line_str]
                else:
                    current_content.append(line_str)
            else:
                if current_section_data:
                    current_content.append(line_str)
                    
        last_sec = finalize_section(current_section_data, current_content)
        if last_sec:
            sections.append(last_sec)
            
        logger.info(f"Regex parser found {len(sections)} sections with hierarchical children")
        
        doc = ParsedDocument()
        doc.act = act
        doc.sections = sections
        return doc

