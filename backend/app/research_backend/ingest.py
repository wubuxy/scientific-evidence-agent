"""Native-text ingestion. OCR and video understanding are separate adapters."""
import io
import re
import zipfile
import posixpath
from pathlib import Path
from xml.etree import ElementTree as ET
from .schemas import SourceBlock, SourceCreate


def parse_upload(filename: str, content: bytes) -> tuple[SourceCreate, str]:
    extension = Path(filename).suffix.lower()
    title = Path(filename.replace("\\", "/")).name[:200] or "未命名资料"
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("文件大小不得超过 20 MiB")
    if extension in {".txt", ".md"}:
        text = content.decode("utf-8-sig")
        blocks = [SourceBlock(text=text)]
        kind = "document"
    elif extension == ".pdf":
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
        if len(reader.pages) > 500:
            raise ValueError("PDF 最多支持 500 页")
        blocks = [SourceBlock(text=text, page=i + 1) for i, page in enumerate(reader.pages)
                  if (text := (page.extract_text() or "").strip())]
        kind = "paper"
    elif extension == ".pptx":
        blocks = []
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("PPTX 解压后超过 100 MiB")
            slides = sorted((name for name in archive.namelist()
                             if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                            key=lambda name: int(re.search(r"slide(\d+)", name).group(1)))
            # Slide filenames are not presentation order after slides are rearranged.
            if "ppt/presentation.xml" in archive.namelist() and "ppt/_rels/presentation.xml.rels" in archive.namelist():
                def read_xml(name):
                    xml = archive.read(name)
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                        raise ValueError("不支持包含实体声明的演示文稿")
                    return ET.fromstring(xml)
                relationships = {r.attrib["Id"]: r.attrib.get("Target", "")
                    for r in read_xml("ppt/_rels/presentation.xml.rels")
                    if r.attrib.get("TargetMode") != "External"}
                ordered = []
                for slide in read_xml("ppt/presentation.xml").iter():
                    if slide.tag == "{http://schemas.openxmlformats.org/presentationml/2006/main}sldId":
                        target = relationships.get(slide.attrib.get(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"), "")
                        name = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "ppt/" + target)
                        if name not in slides:
                            raise ValueError("PPTX 页面关系无效")
                        ordered.append(name)
                slides = ordered
            if len(slides) > 500:
                raise ValueError("PPTX 最多支持 500 页")
            for page, name in enumerate(slides, 1):
                xml = archive.read(name)
                if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    raise ValueError("不支持包含实体声明的演示文稿")
                root = ET.fromstring(xml)
                text = "\n".join(node.text or "" for node in root.iter()
                                 if node.tag == "{http://schemas.openxmlformats.org/drawingml/2006/main}t")
                if text.strip():
                    blocks.append(SourceBlock(text=text, page=page))
        kind = "slides"
    else:
        raise ValueError("当前原生导入支持 .txt/.md/.pdf/.pptx；视频请先导入带时间戳的转写块")
    if not blocks:
        raise ValueError("未提取到原生文本；扫描件、纯图片幻灯片需要另接 OCR/视觉解析")
    return SourceCreate(title=title, kind=kind, blocks=blocks), extension
