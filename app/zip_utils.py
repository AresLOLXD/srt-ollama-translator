import os
import zipfile


def extract_zip(zip_path: str, dest_dir: str) -> list[str]:
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)

    srt_files = []
    for root, _dirs, files in os.walk(dest_dir):
        for name in files:
            if name.lower().endswith(".srt"):
                full_path = os.path.join(root, name)
                srt_files.append(os.path.relpath(full_path, dest_dir))
    return sorted(srt_files)


def create_zip(src_dir: str, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                full_path = os.path.join(root, name)
                zf.write(full_path, os.path.relpath(full_path, src_dir))
