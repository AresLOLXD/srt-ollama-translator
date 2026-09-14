import os
import zipfile


def extract_zip(zip_path: str, dest_dir: str) -> list[str]:
    os.makedirs(dest_dir, exist_ok=True)
    dest_dir_real = os.path.realpath(dest_dir)

    with zipfile.ZipFile(zip_path) as zf:
        # Validate all entries before extracting to prevent zip-slip attacks
        for entry_info in zf.infolist():
            entry_path = os.path.normpath(os.path.join(dest_dir_real, entry_info.filename))
            # Ensure the resolved path is within dest_dir
            if not entry_path.startswith(dest_dir_real + os.sep) and entry_path != dest_dir_real:
                raise ValueError(f"Zip contains unsafe path: {entry_info.filename}")

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


def create_zip_subset(src_dir: str, filenames: list[str], zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in filenames:
            full_path = os.path.join(src_dir, name)
            if os.path.isfile(full_path):
                zf.write(full_path, name)
