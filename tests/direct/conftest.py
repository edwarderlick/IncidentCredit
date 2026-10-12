"""Direct-mode compatibility for GenVM py-genlayer 0.6 (no genlayer.py package).

genlayer-test 0.29.2 still imports `genlayer.py.*`. Runner 5jyc… ships
py-lib-genlayer-std kzr02n, which moved those modules to `genlayer.*`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

import gltest.direct.loader as loader
import gltest.direct.vm as gltest_vm
import gltest.direct.wasi_mock as wasi_mock


def _inject_message_to_fd0(vm) -> None:
    calldata = None
    Address = None
    try:
        import genlayer.calldata as calldata  # type: ignore
        from genlayer.types import Address  # type: ignore
    except ImportError:
        try:
            from genlayer.py import calldata  # type: ignore
            from genlayer.py.types import Address  # type: ignore
        except ImportError:
            return

    sender_addr = vm.sender
    if isinstance(sender_addr, bytes):
        sender_addr = Address(sender_addr)

    contract_addr = vm._contract_address
    if isinstance(contract_addr, bytes):
        contract_addr = Address(contract_addr)

    origin_addr = vm.origin
    if isinstance(origin_addr, bytes):
        origin_addr = Address(origin_addr)

    message_data = {
        "contract_address": contract_addr,
        "sender_address": sender_addr,
        "origin_address": origin_addr,
        "signer_address": origin_addr,
        "stack": [],
        "value": vm._value,
        "datetime": vm._datetime,
        "is_init": False,
        "chain_id": vm._chain_id,
        "entry_kind": 0,
        "entry_data": b"",
        "entry_stage_data": None,
    }
    encoded = calldata.encode(message_data)

    fd, path = tempfile.mkstemp()
    os.write(fd, encoded)
    os.lseek(fd, 0, os.SEEK_SET)
    original_stdin = os.dup(0)
    vm._original_stdin_fd = original_stdin
    vm._message_tmp_path = path
    os.dup2(fd, 0)
    os.close(fd)


def _allocate_contract(contract_cls, vm, *args, **kwargs):
    from genlayer.storage import ROOT_SLOT_ID
    from genlayer.storage._internal.generate import (
        ORIGINAL_INIT_ATTR,
        _BuilderCtx,
        _storage_build,
    )
    from genlayer.storage.root import Root

    Root.MANAGER = vm._storage
    td = _storage_build(_BuilderCtx.empty(), contract_cls)
    slot = vm._storage.get_store_slot(ROOT_SLOT_ID)
    instance = td.get(slot, 0)
    init = getattr(td, "cls", None)
    if init is None:
        init = getattr(contract_cls, "__init__", None)
    else:
        init = getattr(init, "__init__", None)
    if init is not None:
        if hasattr(init, ORIGINAL_INIT_ATTR):
            init = getattr(init, ORIGINAL_INIT_ATTR)
        init(instance, *args, **kwargs)
    return instance


def _calldata_roundtrip_args(args, kwargs):
    try:
        import genlayer.calldata as calldata
    except ImportError:
        try:
            from genlayer.py import calldata  # type: ignore
        except ImportError:
            return args, kwargs

    obj: dict[str, Any] = {}
    if args:
        obj["args"] = list(args)
    if kwargs:
        obj["kwargs"] = kwargs
    if not obj:
        return args, kwargs
    encoded = calldata.encode(obj)
    decoded = calldata.decode(encoded)
    new_args = tuple(decoded.get("args", []))
    new_kwargs = decoded.get("kwargs", {})
    return new_args, new_kwargs


def _patch_run_nondet_for_direct_mode() -> None:
    try:
        import genlayer.vm as gl_vm
    except ImportError:
        try:
            import genlayer.gl.vm as gl_vm  # type: ignore
        except ImportError:
            return

    if getattr(gl_vm, "_direct_mode_patched", False):
        return

    def _direct_run_nondet(leader_fn, validator_fn, /, **kwargs):
        from gltest.direct import wasi_mock

        vm = wasi_mock.get_vm()
        vm._in_nondet = True
        try:
            result = leader_fn()
        finally:
            vm._in_nondet = False
        vm._captured_validators.append((result, leader_fn, validator_fn))
        return result

    def _direct_run_nondet_unsafe(leader_fn, validator_fn, /):
        return _direct_run_nondet(leader_fn, validator_fn)

    try:
        from genlayer.types import Lazy
    except ImportError:
        from genlayer.py.types import Lazy  # type: ignore

    def _lazy_run_nondet(leader_fn, validator_fn, /, **kwargs):
        return Lazy(lambda: _direct_run_nondet(leader_fn, validator_fn, **kwargs))

    def _lazy_run_nondet_unsafe(leader_fn, validator_fn, /):
        return Lazy(lambda: _direct_run_nondet_unsafe(leader_fn, validator_fn))

    _direct_run_nondet.lazy = _lazy_run_nondet
    _direct_run_nondet_unsafe.lazy = _lazy_run_nondet_unsafe

    gl_vm.run_nondet = _direct_run_nondet
    gl_vm.run_nondet_unsafe = _direct_run_nondet_unsafe
    gl_vm._direct_mode_patched = True
    gl_vm._direct_mode_unsafe_patched = True


def _as_addr(addr):
    from genlayer.types import Address

    if addr is None:
        return Address(b"\x00" * 20)
    if isinstance(addr, Address):
        return addr
    if isinstance(addr, bytes):
        return Address(addr)
    if hasattr(addr, "as_bytes"):
        return Address(addr.as_bytes)
    return Address(addr)


def _refresh_gl_message(self) -> None:
    if "genlayer.message" not in sys.modules:
        return
    msg = sys.modules["genlayer.message"]
    from genlayer.types import u256

    sender = _as_addr(self.sender)
    origin = _as_addr(self.origin)
    value = u256(int(self._value))
    msg.sender_address = sender
    msg.origin_address = origin
    msg.signer_address = origin
    msg.value = value
    if hasattr(self, "_contract_address") and self._contract_address is not None:
        msg.contract_address = _as_addr(self._contract_address)
    raw = getattr(msg, "raw", None)
    if isinstance(raw, dict):
        raw["sender_address"] = sender
        raw["origin_address"] = origin
        raw["signer_address"] = origin
        raw["value"] = value
        raw["datetime"] = self._datetime


_orig_wasi_gl_call = wasi_mock.gl_call


def gl_call(data: bytes, /) -> int:
    """Decode/encode gl_call payloads with genlayer.calldata (0.6 SDK)."""
    import genlayer.calldata as calldata

    # Provide the import path genlayer-test 0.29.2 still uses.
    fake_py = type(sys)("genlayer.py")
    fake_py.calldata = calldata
    import genlayer.types as gl_types

    fake_py.types = gl_types
    sys.modules["genlayer.py"] = fake_py
    sys.modules["genlayer.py.calldata"] = calldata
    sys.modules["genlayer.py.types"] = gl_types
    return _orig_wasi_gl_call(data)


def _handle_llm_request(vm, data):
    """Keep `ok` as a JSON string; 0.6 exec_prompt(json) json.loads it."""
    prompt = data.get("prompt", "") if isinstance(data, dict) else ""
    response = vm._match_llm_mock(prompt)
    if response is None:
        raise RuntimeError(f"No LLM mock for prompt: {str(prompt)[:120]}")
    if isinstance(response, (dict, list)):
        import json as _json

        response = _json.dumps(response)
    return {"ok": response}


loader._inject_message_to_fd0 = _inject_message_to_fd0
loader._allocate_contract = _allocate_contract
loader._calldata_roundtrip_args = _calldata_roundtrip_args
loader._patch_run_nondet_for_direct_mode = _patch_run_nondet_for_direct_mode
gltest_vm.VMContext._refresh_gl_message = _refresh_gl_message
wasi_mock.gl_call = gl_call
wasi_mock._handle_llm_request = _handle_llm_request
