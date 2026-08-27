"""Deteccao de tipo de arquivo por conteudo (magic bytes / mimeType do Drive),
usada como fallback quando o NOME do arquivo nao tem uma extensao reconhecida
(ou nenhuma extensao) - caso real visto em producao: aula de audio salva no
Drive sem nenhuma extensao no nome (ex.: "Oficina de comunicação", ~45MB),
que um scanner baseado só em extensão nunca detectava.

`kind` retornado é sempre "slide" ou "audio" (os dois tipos que o pipeline sabe
processar hoje); None quando não dá pra identificar com confiança.
"""
import io
import zipfile
from typing import Optional, Tuple

# --- Assinaturas de áudio (magic bytes no início do arquivo) ---------------
_AUDIO_SIGNATURES: Tuple[Tuple[bytes, str], ...] = (
    (b"ID3", ".mp3"),               # MP3 com tag ID3v2 no início
    (b"OggS", ".ogg"),
    (b"fLaC", ".flac"),
    (b"\x30\x26\xb2\x75", ".wma"),  # GUID de cabeçalho ASF/WMA
)

# --- Assinaturas de documento/apresentação ----------------------------------
_PDF_SIGNATURE = b"%PDF-"
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # .doc/.ppt/.xls legado (OLE2/CFB)

# mimeType do Drive -> (kind, extensão a usar ao salvar localmente). Cobre só
# tipos que dá pra baixar direto via files.get_media (arquivos binários reais);
# formatos nativos do Google (Docs/Slides) exigiriam files.export e não têm
# conteúdo próprio, por isso ficam de fora de propósito.
_MIME_TO_KIND = {
    "audio/mpeg": ("audio", ".mp3"),
    "audio/mp3": ("audio", ".mp3"),
    "audio/x-m4a": ("audio", ".m4a"),
    "audio/mp4": ("audio", ".m4a"),
    "audio/aac": ("audio", ".aac"),
    "audio/wav": ("audio", ".wav"),
    "audio/x-wav": ("audio", ".wav"),
    "audio/wave": ("audio", ".wav"),
    "audio/ogg": ("audio", ".ogg"),
    "audio/flac": ("audio", ".flac"),
    "audio/x-flac": ("audio", ".flac"),
    "audio/webm": ("audio", ".weba"),
    "application/pdf": ("slide", ".pdf"),
    "application/vnd.ms-powerpoint": ("slide", ".ppt"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("slide", ".pptx"),
    "application/msword": ("slide", ".doc"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("slide", ".docx"),
    "application/vnd.oasis.opendocument.presentation": ("slide", ".odp"),
    "application/vnd.oasis.opendocument.text": ("slide", ".odt"),
    "application/rtf": ("slide", ".rtf"),
    "text/plain": ("slide", ".txt"),
}


def sniff_kind_from_mime_type(mime_type: Optional[str]) -> Optional[Tuple[str, str]]:
    """Classifica por mimeType (ex.: vindo da Drive API, que detecta o tipo real
    no upload independente do nome do arquivo). Mais barato que baixar o arquivo
    inteiro só pra olhar os bytes - usar isso primeiro sempre que disponível."""
    if not mime_type:
        return None
    mime_type = mime_type.split(";")[0].strip().lower()
    result = _MIME_TO_KIND.get(mime_type)
    if result:
        return result
    if mime_type.startswith("audio/"):
        return ("audio", ".mp3")  # formato de áudio não mapeado - assume mp3 como extensão genérica
    return None


def sniff_kind_from_bytes(data: bytes) -> Optional[Tuple[str, str]]:
    """Classifica pelos bytes reais do arquivo (magic numbers). Espera o arquivo
    INTEIRO (não só um prefixo) quando possível - necessário pra inspecionar o
    conteúdo interno de contêineres ZIP (.pptx/.docx/.odp/.odt) e o box "ftyp"
    de containers ISO-BMFF (M4A/MP4), que podem não estar nos primeiros bytes."""
    if not data:
        return None

    # MP3 sem tag ID3 - frame sync: primeiro byte 0xFF, segundo com os 3 bits
    # mais altos em 1 (0xE0 mascarado).
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return ("audio", ".mp3")

    for sig, ext in _AUDIO_SIGNATURES:
        if data.startswith(sig):
            return ("audio", ext)

    # RIFF sozinho não basta (AVI também começa com RIFF) - confirma "WAVE" no offset 8.
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WAVE":
        return ("audio", ".wav")

    # Containers ISO-BMFF (M4A/MP4 áudio) - box "ftyp" a partir do offset 4.
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"M4A ", b"M4B ", b"isom", b"mp42", b"iso2", b"3gp4", b"qt  "):
            return ("audio", ".m4a")

    if data.startswith(_PDF_SIGNATURE):
        return ("slide", ".pdf")

    if data.startswith(_OLE_SIGNATURE):
        # OLE2 legado: pode ser .doc, .ppt ou .xls - sem abrir o CFB inteiro não
        # dá pra saber qual com certeza. Assume .ppt (o caso mais comum em
        # material de aula) - o Gemini lê o conteúdo mesmo com a extensão entre
        # .doc/.ppt/.xls "errada".
        return ("slide", ".ppt")

    if data.startswith(b"PK\x03\x04"):
        return _sniff_zip_kind(data)

    return None


def _sniff_zip_kind(data: bytes) -> Optional[Tuple[str, str]]:
    """OOXML (.pptx/.docx/.xlsx) e ODF (.odp/.odt) são, por baixo, arquivos ZIP -
    olha os nomes internos pra distinguir com certeza em vez de chutar."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if any(n.startswith("ppt/") for n in names):
                return ("slide", ".pptx")
            if any(n.startswith("word/") for n in names):
                return ("slide", ".docx")
            if "mimetype" in names:
                try:
                    mt = zf.read("mimetype").decode("utf-8", errors="ignore")
                    if "presentation" in mt:
                        return ("slide", ".odp")
                    if "text" in mt or "document" in mt:
                        return ("slide", ".odt")
                except Exception:
                    pass
    except Exception:
        pass
    # ZIP reconhecido mas não deu pra classificar com certeza (ex.: só tinha os
    # primeiros bytes, sem o diretório central) - assume apresentação, o caso
    # mais comum de material de aula.
    return ("slide", ".pptx")
