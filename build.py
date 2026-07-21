import os, sys, shutil, subprocess, pathlib

ROOT = pathlib.Path(__file__).parent
DIST = ROOT / "dist"

def clean():
    for p in ["build", "dist"]:
        p = ROOT / p
        if p.exists():
            shutil.rmtree(p)
    print("Cleaned build/ and dist/")

def build_all():
    DIST.mkdir(parents=True, exist_ok=True)
    
    for spec in ["Prank.spec", "Prank_Test.spec"]:
        print(f"\n=== Building {spec} ===")
        subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / spec)], check=True, cwd=ROOT)
    
    for spec in ["Setup.spec", "GachiRemix.spec"]:
        print(f"\n=== Building {spec} ===")
        subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / spec)], check=True, cwd=ROOT)
    
    print("\nDone! Files in dist/:")
    for f in sorted(DIST.iterdir()):
        sz = f.stat().st_size
        print(f"  {f.name} ({sz / 1024 / 1024:.1f} MB)" if sz > 1024*1024 else f"  {f.name} ({sz / 1024:.0f} KB)")

if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    build_all()
