from pathlib import Path

def meme_file(filepath, roots):
    image = Path(filepath).resolve(strict=True)
    allowed = [Path(root).resolve() for root in roots]
    if not any(image.is_relative_to(root) for root in allowed):
        raise RuntimeError('meme-file-denied')
    if not image.is_file() or image.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp') or image.stat().st_size > 20 * 1024 * 1024:
        raise RuntimeError('meme-file-denied')
    return image
