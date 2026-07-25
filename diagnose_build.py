import subprocess
import sys
import os
import traceback

def diagnose_build(site_dir):
    build_py = os.path.join(site_dir, 'build.py')

    if not os.path.exists(build_py):
        print(f"[ERROR] build.py 不存在: {build_py}")
        return

    print(f"[INFO] 诊断站点: {site_dir}")
    print(f"[INFO] build.py 路径: {build_py}")
    print("=" * 60)

    # 先检查文件编码和语法
    try:
        with open(build_py, 'r', encoding='utf-8') as f:
            content = f.read()
        compile(content, build_py, 'exec')
        print("[OK] build.py 语法检查通过")
    except SyntaxError as e:
        print(f"[FAIL] build.py 语法错误!")
        print(f"  文件: {e.filename}")
        print(f"  行号: {e.lineno}")
        print(f"  列号: {e.offset}")
        print(f"  错误: {e.msg}")
        print(f"  代码: {e.text}")
        return
    except Exception as e:
        print(f"[FAIL] 读取 build.py 失败: {e}")
        return

    # 运行 build.py 并捕获完整输出
    print("[INFO] 开始运行 build.py...")
    print("-" * 60)

    try:
        result = subprocess.run(
            [sys.executable, build_py],
            cwd=site_dir,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            print("[OK] build.py 运行成功!")
        else:
            print(f"[FAIL] build.py 返回码: {result.returncode}")

        if result.stdout:
            print("[STDOUT]:")
            print(result.stdout)

        if result.stderr:
            print("[STDERR]:")
            print(result.stderr)

    except subprocess.TimeoutExpired:
        print("[FAIL] build.py 运行超时 (60秒)")
    except Exception as e:
        print(f"[FAIL] 运行 build.py 异常: {e}")
        traceback.print_exc()

    print("=" * 60)
    print("[INFO] 诊断完成")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='诊断 build.py 失败原因')
    parser.add_argument('site_dir', help='站点目录路径')
    args = parser.parse_args()
    diagnose_build(args.site_dir)
