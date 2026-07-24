"""TensorRT drop-in for onnxruntime.InferenceSession (Python 3.6 compatible).

Used by clip_frames.FrameEmbedder when onnxruntime is not installed.
TensorRT and its Python bindings ship with JetPack 4.6+, so no extra
dependencies are required beyond pycuda.

Usage:
    session = TRTSession(engine_path_or_onnx_path)
    outputs = session.run(None, {session.get_inputs()[0].name: arr})

The engine is built from the ONNX file on first call and cached to
<model_dir>/image_encoder.trt (build takes 3-8 min on Nano, runs at GPU speed
thereafter).  FP16 is enabled when the device supports it.

Python 3.6 compatibility: uses % formatting, object-style classes, no f-strings
with = specifier.
"""

from __future__ import print_function

import os
import sys
import numpy as np


def _build_engine(onnx_path, engine_path, fp16=True, workspace_mb=512):
    """Parse an ONNX file and serialise a TRT engine to disk."""
    try:
        import tensorrt as trt
    except ImportError:
        raise RuntimeError("tensorrt not found — install JetPack or `pip install tensorrt`")

    logger = trt.Logger(trt.Logger.WARNING)
    EXPLICIT_BATCH = 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)

    print("[trt_session] Building TRT engine from %s (this takes a few minutes)…" % onnx_path,
          file=sys.stderr)
    with trt.Builder(logger) as builder, \
         builder.create_network(EXPLICIT_BATCH) as network, \
         trt.OnnxParser(network, logger) as parser:

        cfg = builder.create_builder_config()
        cfg.max_workspace_size = workspace_mb << 20
        if fp16 and builder.platform_has_fast_fp16:
            cfg.set_flag(trt.BuilderFlag.FP16)
            print("[trt_session] FP16 enabled", file=sys.stderr)

        with open(onnx_path, "rb") as f:
            if not parser.parse(f.read()):
                for i in range(parser.num_errors):
                    print("[trt_session] parse error: %s" % parser.get_error(i), file=sys.stderr)
                raise RuntimeError("ONNX parse failed — check TRT version vs opset")

        # Resolve any dynamic batch axis at build time (batch = 1)
        profile = builder.create_optimization_profile()
        in_name = network.get_input(0).name
        in_shape = tuple(network.get_input(0).shape)
        fixed_shape = tuple(1 if d < 0 else d for d in in_shape)
        profile.set_shape(in_name, fixed_shape, fixed_shape, fixed_shape)
        cfg.add_optimization_profile(profile)

        engine = builder.build_engine(network, cfg)
        if engine is None:
            raise RuntimeError("TRT engine build failed")

    with open(engine_path, "wb") as f:
        f.write(engine.serialize())
    print("[trt_session] Engine saved to %s" % engine_path, file=sys.stderr)
    return engine


class TRTSession(object):
    """Minimal onnxruntime.InferenceSession lookalike backed by TensorRT."""

    def __init__(self, model_path, providers=None):
        """
        model_path: path to image_encoder.onnx OR a pre-built .trt engine.
        If the .trt engine doesn't exist alongside the ONNX, it is built once
        and cached.  providers is ignored (TRT always runs on GPU).
        """
        try:
            import tensorrt as trt
            import pycuda.autoinit  # noqa: F401 — initialises CUDA context
            import pycuda.driver as cuda
        except ImportError as e:
            raise RuntimeError(
                "TRTSession requires tensorrt and pycuda: %s\n"
                "Install pycuda with: pip3 install 'pycuda==2021.1'" % e
            )

        self._cuda = cuda

        # Determine engine path (alongside ONNX, with .trt extension)
        if model_path.endswith(".trt"):
            engine_path = model_path
            onnx_path = model_path.replace(".trt", ".onnx")
        else:
            onnx_path = model_path
            engine_path = os.path.splitext(model_path)[0] + ".trt"

        if not os.path.exists(engine_path):
            _build_engine(onnx_path, engine_path)

        logger = trt.Logger(trt.Logger.WARNING)
        with open(engine_path, "rb") as f:
            self._engine = trt.Runtime(logger).deserialize_cuda_engine(f.read())

        self._context = self._engine.create_execution_context()

        # Identify input / output bindings (works for single-input models)
        self._in_idx = None
        self._out_idx = None
        for i in range(self._engine.num_bindings):
            if self._engine.binding_is_input(i):
                self._in_idx = i
                self._in_name = self._engine.get_binding_name(i)
            else:
                self._out_idx = i

        in_shape  = tuple(self._engine.get_binding_shape(self._in_idx))
        out_shape = tuple(self._engine.get_binding_shape(self._out_idx))

        # Resolve any remaining -1 dims (dynamic batch kept as 1)
        in_shape  = tuple(1 if d < 0 else d for d in in_shape)
        out_shape = tuple(1 if d < 0 else d for d in out_shape)

        self._context.set_binding_shape(self._in_idx, in_shape)
        self._in_shape  = in_shape
        self._out_shape = out_shape

        self._in_buf  = cuda.mem_alloc(int(np.prod(in_shape))  * 4)  # float32
        self._out_buf = cuda.mem_alloc(int(np.prod(out_shape)) * 4)
        self._bindings = [None] * self._engine.num_bindings
        self._bindings[self._in_idx]  = int(self._in_buf)
        self._bindings[self._out_idx] = int(self._out_buf)

        print("[trt_session] TRT engine ready (in=%s out=%s)" % (in_shape, out_shape),
              file=sys.stderr)

    def run(self, output_names, feed):
        """Mimic ort.InferenceSession.run(); returns [output_array]."""
        arr = list(feed.values())[0].astype(np.float32)
        self._cuda.memcpy_htod(self._in_buf, np.ascontiguousarray(arr))
        self._context.execute_v2(self._bindings)
        out = np.empty(self._out_shape, dtype=np.float32)
        self._cuda.memcpy_dtoh(out, self._out_buf)
        return [out]

    def get_inputs(self):
        class _Binding(object):
            pass
        b = _Binding()
        b.name = self._in_name
        return [b]

    def get_providers(self):
        return ["TensorrtExecutionProvider"]
