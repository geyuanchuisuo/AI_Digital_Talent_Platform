"""Local OCR in a killable process; model preparation never runs implicitly."""
from __future__ import annotations

import argparse
from importlib import resources
import multiprocessing
from pathlib import Path
import shutil


class OCRFailure(RuntimeError):
    pass


def model_paths(directory: Path) -> dict[str, str]:
    paths = {kind: directory / f"{kind}.onnx" for kind in ("det", "cls", "rec")}
    missing = [p.name for p in paths.values() if not p.is_file()]
    if missing:
        raise OCRFailure("OCR 模型缺失：" + ", ".join(missing) + "；请先执行 OCR 模型预备命令")
    return {kind: str(path) for kind, path in paths.items()}


def prepare_models(directory: Path) -> None:
    """Copy models shipped by the declared wheel; no runtime network download."""
    bundled = Path(str(resources.files("rapidocr_onnxruntime"))) / "models"
    selected = {}
    for kind in ("det", "cls", "rec"):
        candidates = sorted(bundled.glob(f"*{kind}*.onnx"))
        if not candidates:
            raise OCRFailure(f"已安装 OCR 包未提供 {kind} 模型")
        selected[kind] = candidates[0]
    directory.mkdir(parents=True, exist_ok=True)
    for kind, source in selected.items():
        target = directory / f"{kind}.onnx"
        if not target.exists():
            shutil.copyfile(source, target)


def _worker(connection, image: bytes, models: dict[str, str]) -> None:
    try:
        from rapidocr_onnxruntime import RapidOCR
        # Legacy 1.2 has no thread-count kwargs. Restrict its options factory only
        # inside this disposable process; never patch installed files or the API process.
        import rapidocr_onnxruntime.utils as legacy_utils
        if hasattr(legacy_utils, "SessionOptions"):
            from onnxruntime import SessionOptions
            def bounded_options():
                options = SessionOptions()
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                return options
            legacy_utils.SessionOptions = bounded_options
        engine = RapidOCR(det_model_path=models["det"], cls_model_path=models["cls"],
                          rec_model_path=models["rec"], intra_op_num_threads=1,
                          inter_op_num_threads=1)
        result, _ = engine(image)
        lines = [(str(line[1]), float(line[2])) for line in (result or []) if line[1]]
        connection.send((True, "\n".join(line[0] for line in lines),
                         min((line[1] for line in lines), default=None)))
    except Exception as exc:
        connection.send((False, f"OCR 执行失败：{type(exc).__name__}: {exc}", None))
    finally:
        connection.close()


def recognize(image: bytes, *, timeout: float, directory: Path) -> tuple[str, float | None]:
    if timeout <= 0:
        raise OCRFailure("OCR 超时，请重试或减少文件页数")
    models = model_paths(directory)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(child, image, models), daemon=True)
    try:
        process.start()
        child.close()
        if not parent.poll(timeout):
            raise OCRFailure("OCR 超时，请重试或减少文件页数")
        try:
            ok, text, confidence = parent.recv()
        except EOFError as exc:
            raise OCRFailure("OCR 进程异常退出，请检查依赖和模型") from exc
        if not ok:
            raise OCRFailure(text)
        return text, confidence
    finally:
        child.close()
        parent.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()


if __name__ == "__main__":
    from app.config import BASE_DIR, settings
    parser = argparse.ArgumentParser(description="Prepare bundled local OCR models")
    parser.add_argument("--prepare-models", action="store_true", required=True)
    args = parser.parse_args()
    directory = Path(settings.ocr_model_dir)
    if not directory.is_absolute():
        directory = BASE_DIR / directory
    prepare_models(directory)
    print("OCR models prepared:", directory)
