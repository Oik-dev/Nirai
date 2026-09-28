from pathlib import Path
for p in [
    Path(r"D:\Products\Nirai\v2\audit_doc_noise_tmp.py"),
    Path(r"D:\Products\Nirai\v2\audit_section_numbers_tmp.py"),
]:
    p.unlink(missing_ok=True)
print("clean")
