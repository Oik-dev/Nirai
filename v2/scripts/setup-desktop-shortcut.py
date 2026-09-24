from __future__ import annotations

import ctypes
import struct
import uuid
from ctypes import wintypes
from pathlib import Path


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid(value: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def com_method(instance: ctypes.c_void_p, index: int, restype, *argtypes):
    vtable = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def check_hr(hr: int, action: str) -> None:
    if hr < 0:
        raise OSError(f"{action} failed: HRESULT 0x{hr & 0xffffffff:08X}")


def desktop_path() -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    # CSIDL_DESKTOPDIRECTORY = 0x0010
    hr = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer)
    check_hr(hr, "SHGetFolderPathW")
    return Path(buffer.value)


def write_ico(png_path: Path, ico_path: Path) -> tuple[int, int]:
    data = png_path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"Not a PNG file: {png_path}")

    width, height = struct.unpack(">II", data[16:24])
    if not (1 <= width <= 256 and 1 <= height <= 256):
        raise ValueError(f"Icon PNG must be 1..256 px, got {width}x{height}")

    ico_path.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        0 if width == 256 else width,
        0 if height == 256 else height,
        0,
        0,
        1,
        32,
        len(data),
        22,
    )
    ico_path.write_bytes(header + entry + data)
    return width, height


def create_shortcut(
    shortcut_path: Path,
    target_path: Path,
    arguments: str,
    working_directory: Path,
    icon_path: Path,
    description: str,
) -> None:
    ole32 = ctypes.windll.ole32
    check_hr(ole32.CoInitialize(None), "CoInitialize")

    shell_link = ctypes.c_void_p()
    persist_file = ctypes.c_void_p()
    clsid_shell_link = guid("00021401-0000-0000-C000-000000000046")
    iid_shell_link_w = guid("000214F9-0000-0000-C000-000000000046")
    iid_persist_file = guid("0000010B-0000-0000-C000-000000000046")

    try:
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid_shell_link),
            None,
            1,  # CLSCTX_INPROC_SERVER
            ctypes.byref(iid_shell_link_w),
            ctypes.byref(shell_link),
        )
        check_hr(hr, "CoCreateInstance(IShellLinkW)")

        set_description = com_method(shell_link, 7, ctypes.c_long, wintypes.LPCWSTR)
        set_working_directory = com_method(shell_link, 9, ctypes.c_long, wintypes.LPCWSTR)
        set_arguments = com_method(shell_link, 11, ctypes.c_long, wintypes.LPCWSTR)
        set_show_cmd = com_method(shell_link, 15, ctypes.c_long, ctypes.c_int)
        set_icon_location = com_method(shell_link, 17, ctypes.c_long, wintypes.LPCWSTR, ctypes.c_int)
        set_path = com_method(shell_link, 20, ctypes.c_long, wintypes.LPCWSTR)

        check_hr(set_path(shell_link, str(target_path)), "IShellLinkW.SetPath")
        check_hr(set_arguments(shell_link, arguments), "IShellLinkW.SetArguments")
        check_hr(set_working_directory(shell_link, str(working_directory)), "IShellLinkW.SetWorkingDirectory")
        check_hr(set_description(shell_link, description), "IShellLinkW.SetDescription")
        check_hr(set_show_cmd(shell_link, 1), "IShellLinkW.SetShowCmd")
        check_hr(set_icon_location(shell_link, str(icon_path), 0), "IShellLinkW.SetIconLocation")

        query_interface = com_method(
            shell_link,
            0,
            ctypes.c_long,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        check_hr(
            query_interface(shell_link, ctypes.byref(iid_persist_file), ctypes.byref(persist_file)),
            "QueryInterface(IPersistFile)",
        )

        save = com_method(persist_file, 6, ctypes.c_long, wintypes.LPCWSTR, wintypes.BOOL)
        check_hr(save(persist_file, str(shortcut_path), True), "IPersistFile.Save")
    finally:
        if persist_file.value:
            com_method(persist_file, 2, wintypes.ULONG)(persist_file)
        if shell_link.value:
            com_method(shell_link, 2, wintypes.ULONG)(shell_link)
        ole32.CoUninitialize()


def main() -> None:
    v2_root = Path(__file__).resolve().parent.parent
    repo_root = v2_root.parent
    png_path = repo_root / "Img" / "Nirai v2 Icon.png"
    ico_path = v2_root / "resources" / "nirai-v2.ico"
    launcher_path = v2_root / "Launch Nirai v2.vbs"
    wscript_path = Path(ctypes.create_unicode_buffer(32768).value)

    width, height = write_ico(png_path, ico_path)

    system_root = Path(__import__("os").environ["SystemRoot"])
    wscript_path = system_root / "System32" / "wscript.exe"
    shortcut_path = desktop_path() / "Nirai v2.lnk"

    create_shortcut(
        shortcut_path=shortcut_path,
        target_path=wscript_path,
        arguments=f'"{launcher_path}"',
        working_directory=v2_root,
        icon_path=ico_path,
        description="Nirai v2 を起動。起動中なら安全に再起動し、ウィンドウを開きます。",
    )

    for obsolete in (
        v2_root / "scripts" / "setup-icon.py",
        v2_root / "scripts" / "probe_shortcut_deps.py",
        v2_root / "scripts" / "setup-desktop-shortcut.ps1",
    ):
        if obsolete.exists():
            obsolete.unlink()

    print(f"ICON={ico_path}")
    print(f"ICON_SIZE={width}x{height}")
    print(f"SHORTCUT={shortcut_path}")


if __name__ == "__main__":
    main()
