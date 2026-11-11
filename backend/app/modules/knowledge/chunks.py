"""Pure source-preserving segmentation, independent of public registration."""


def make_chunks(pages, *, max_chars=1000, overlap=120):
    if type(max_chars) is not int or type(overlap) is not int or not 0 <= overlap < max_chars:
        raise ValueError('分段长度或重叠长度无效')
    output = []
    for page in pages:
        if page.get('quality_status', 'ready') not in ('ready', 'empty'):
            raise ValueError('原文页面未完整处理，不能建立完整知识索引')
        text = page.get('text', '')
        if not isinstance(text, str):
            raise ValueError('原文格式无效')
        if not text.strip():
            continue
        source_type = page.get('source_type', 'text')
        page_no = None if source_type in ('document', 'txt') else page.get('page_no')
        if source_type not in ('document', 'txt') and (type(page_no) is not int or page_no < 1):
            raise ValueError('缺少真实来源页码')
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            snippet = text[start:end]
            if snippet.strip():
                output.append({'chunk_no': len(output), 'page_no': page_no,
                    'source_type': source_type, 'offset_start': start, 'offset_end': end, 'text': snippet})
            if end == len(text):
                break
            start = end - overlap
    if not output:
        raise ValueError('没有可索引的有效原文')
    return output
