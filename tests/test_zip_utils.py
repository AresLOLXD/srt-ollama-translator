import os
import zipfile
from app.zip_utils import extract_zip, create_zip


def test_extract_zip_finds_srt_files_recursively(tmp_path):
    zip_path = tmp_path / "input.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("episode1.srt", "content1")
        zf.writestr("subdir/episode2.srt", "content2")
        zf.writestr("readme.txt", "not a subtitle")

    dest_dir = tmp_path / "extracted"
    srt_files = extract_zip(str(zip_path), str(dest_dir))

    assert srt_files == ["episode1.srt", os.path.join("subdir", "episode2.srt")]
    assert (dest_dir / "episode1.srt").read_text() == "content1"


def test_extract_zip_returns_empty_list_when_no_srt_files(tmp_path):
    zip_path = tmp_path / "input.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "no subtitles here")

    dest_dir = tmp_path / "extracted"
    assert extract_zip(str(zip_path), str(dest_dir)) == []


def test_create_zip_packages_all_files_preserving_structure(tmp_path):
    src_dir = tmp_path / "output"
    os.makedirs(src_dir / "subdir")
    (src_dir / "episode1.srt").write_text("translated1")
    (src_dir / "subdir" / "episode2.srt").write_text("translated2")

    zip_path = tmp_path / "result.zip"
    create_zip(str(src_dir), str(zip_path))

    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
        assert names == ["episode1.srt", os.path.join("subdir", "episode2.srt")]


def test_extract_zip_rejects_path_traversal_attacks(tmp_path):
    """Verify that extract_zip rejects entries that escape dest_dir (zip-slip vulnerability)."""
    zip_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("normal.srt", "safe content")
        zf.writestr("../evil.txt", "escaped content")

    dest_dir = tmp_path / "extracted"

    # Should raise ValueError when detecting unsafe path
    try:
        extract_zip(str(zip_path), str(dest_dir))
        assert False, "extract_zip should raise ValueError for path traversal"
    except ValueError as e:
        assert "unsafe path" in str(e).lower()
        # Verify the evil file was NOT created outside dest_dir
        assert not (tmp_path / "evil.txt").exists()


def test_extract_zip_rejects_absolute_path_entries(tmp_path):
    """Verify that extract_zip rejects entries with absolute paths."""
    zip_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("normal.srt", "safe")
        zf.writestr("/etc/passwd", "evil")

    dest_dir = tmp_path / "extracted"

    try:
        extract_zip(str(zip_path), str(dest_dir))
        assert False, "extract_zip should raise ValueError for absolute paths"
    except ValueError as e:
        assert "unsafe path" in str(e).lower()
