"""
python_deps.py — 统一依赖检查与自动安装
用法: import python_deps; python_deps.ensure()
"""
import subprocess
import sys
import os

REQUIREMENTS_FILE = "/home/node/.openclaw/workspace/requirements.txt"
_PIP = None


def _get_pip():
    global _PIP
    if _PIP:
        return _PIP
    try:
        import pip
        _PIP = [sys.executable, "-m", "pip"]
        return _PIP
    except ImportError:
        for p in ["pip3", "pip", "/home/node/.local/bin/pip3"]:
            r = subprocess.run([p, "--version"], capture_output=True)
            if r.returncode == 0:
                _PIP = [p]
                return _PIP
        _PIP = [sys.executable, "-m", "ensurepip", "--upgrade"]
        subprocess.run(_PIP, capture_output=True)
        _PIP = [sys.executable, "-m", "pip"]
        return _PIP


def _pkg_importable(pkg: str) -> bool:
    try:
        __import__(pkg.replace("-", "_"))
        return True
    except ImportError:
        return False


def _extract_pkgs() -> list:
    pkgs = []
    if not os.path.exists(REQUIREMENTS_FILE):
        return pkgs
    with open(REQUIREMENTS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg = line.split(">")[0].split("<")[0].split("=")[0].split("!")[0].strip()
            if pkg:
                pkgs.append(pkg)
    return pkgs


def ensure():
    """检查并安装缺失依赖。启动程序时调用一次即可。"""
    print("[deps] 检查 Python 依赖...")
    missing = []
    for pkg in _extract_pkgs():
        if _pkg_importable(pkg):
            print(f"  ✓ {pkg}")
        else:
            print(f"  ✗ {pkg} — 将安装")
            missing.append(pkg)

    if not missing:
        print("[deps] 所有依赖已满足")
        return

    print(f"[deps] 安装 {len(missing)} 个缺失包...")
    pip = _get_pip()
    try:
        subprocess.run(
            pip + ["install", "--break-system-packages"] + missing,
            check=True,
            capture_output=True
        )
        print("[deps] 安装完成")
    except subprocess.CalledProcessError as e:
        print(f"[deps] 安装失败: {e.stderr.decode()[-500:]}")
        raise


if __name__ == "__main__":
    ensure()
