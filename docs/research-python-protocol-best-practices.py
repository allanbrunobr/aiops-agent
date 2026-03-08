"""
Python Protocol Best Practices -- Research Document
====================================================
Python 3.12+ | from __future__ import annotations
Compiled: 2026-03-08

This file is executable Python -- every example can be run or type-checked.
Run:  python docs/research-python-protocol-best-practices.py
Check: mypy docs/research-python-protocol-best-practices.py --strict
"""

from __future__ import annotations

from typing import (
    Any,
    Protocol,
    runtime_checkable,
)
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
import asyncio


# =============================================================================
# 1. BASIC PROTOCOL DEFINITION
# =============================================================================
#
# Protocol enables *structural subtyping* (static duck typing).
# A class satisfies a Protocol if it has the required methods/attributes
# with compatible signatures -- NO inheritance required.
#
# Import: from typing import Protocol, runtime_checkable
# (NOT from typing_extensions -- these are stable in 3.12+)

class Greeter(Protocol):
    """Minimal protocol -- just declare method signatures with `...` body."""

    def greet(self, name: str) -> str: ...


# This class satisfies Greeter WITHOUT inheriting from it:
class EnglishGreeter:
    def greet(self, name: str) -> str:
        return f"Hello, {name}!"


def use_greeter(g: Greeter) -> None:
    print(g.greet("World"))


# =============================================================================
# 2. @runtime_checkable -- WHEN TO USE IT (AND WHEN NOT TO)
# =============================================================================
#
# @runtime_checkable adds isinstance() support, but with MAJOR caveats:
#
# WHAT IT CHECKS:  presence of required method/attribute NAMES
# WHAT IT IGNORES: method SIGNATURES, return types, attribute types
#
# WHEN TO USE:
#   - Validation at system boundaries (plugin loading, deserialization)
#   - Defensive checks in public APIs that accept arbitrary objects
#   - Debug assertions
#
# WHEN NOT TO USE:
#   - Performance-critical hot loops (isinstance on Protocol is slow)
#   - As a substitute for static type checking (mypy/pyright are better)
#   - When you need signature verification (it won't catch mismatches)

@runtime_checkable
class Closeable(Protocol):
    def close(self) -> None: ...


class FileWrapper:
    def close(self) -> None:
        print("closed")


class FakeCloseable:
    def close(self, force: bool) -> None:  # WRONG signature -- but isinstance won't catch it!
        print("force closed")


def demo_runtime_checkable() -> None:
    fw = FileWrapper()
    fc = FakeCloseable()

    print(isinstance(fw, Closeable))  # True  -- correct
    print(isinstance(fc, Closeable))  # True  -- MISLEADING! Signature mismatch not detected.

    # Best practice: use isinstance at boundaries, rely on mypy for correctness
    assert isinstance(fw, Closeable), "Expected a Closeable"


# =============================================================================
# 3. PROTOCOL vs ABC -- DECISION GUIDE (Python 3.12+)
# =============================================================================
#
# USE PROTOCOL WHEN:
#   - You want structural (duck) typing -- no forced inheritance
#   - You don't control the implementing classes (third-party code)
#   - You want lightweight interfaces with minimal boilerplate
#   - You're defining "capability" interfaces (Closeable, Serializable)
#   - You want to type-hint callback shapes or strategy patterns
#
# USE ABC WHEN:
#   - You control the class hierarchy and WANT enforced inheritance
#   - You need runtime enforcement (ABC raises TypeError on instantiation
#     if abstract methods are missing -- Protocol does NOT)
#   - You have shared implementation logic in base class methods
#   - You need __init_subclass__ hooks or class registration
#   - You're building a framework where subclassing IS the contract
#
# MODERN CONSENSUS (2025-2026):
#   Prefer Protocol for interface definitions. Use ABC only when you need
#   runtime enforcement or shared implementation. Many codebases are
#   migrating from ABC to Protocol for cleaner dependency injection.


# =============================================================================
# 4. PROTOCOL + PYDANTIC BaseModel
# =============================================================================
#
# CAN a Pydantic model implement a Protocol?  YES, with caveats.
#
# WHAT WORKS:
#   - A Pydantic model that has the required methods/attributes
#     satisfies a Protocol for STATIC type checking (mypy/pyright).
#   - With @runtime_checkable, isinstance() also works (checks method names).
#
# WHAT DOES NOT WORK:
#   - You CANNOT do `class MyProto(BaseModel, Protocol)` -- Protocol
#     cannot derive from non-Protocol classes.
#   - Protocol cannot express Pydantic's __init__ signature (field kwargs).
#   - Pydantic closed this as "not planned" (github issue #11878).
#
# PRACTICAL PATTERN:

# Suppose you want a Protocol for "anything that can be serialized":
class Serializable(Protocol):
    def model_dump(self) -> dict[str, Any]: ...


# A Pydantic model already has model_dump(), so it satisfies this:
#
#   from pydantic import BaseModel
#   class User(BaseModel):
#       name: str
#       email: str
#   # User satisfies Serializable -- mypy and isinstance both pass.
#
# For Protocol FIELDS in Pydantic models, use:
#   class Config:
#       arbitrary_types_allowed = True


# =============================================================================
# 5. METHOD SIGNATURES -- BEST PRACTICES
# =============================================================================
#
# Rule 1: Use `...` (Ellipsis) as the method body, not `pass`
# Rule 2: Always provide full type hints on all parameters and return
# Rule 3: Use `self` as first param (it's structural, but conventional)
# Rule 4: For optional params, match signatures exactly
# Rule 5: Declare instance attributes at CLASS level, not in __init__

class Repository(Protocol):
    """Well-typed Protocol with properties, methods, and class-level attributes."""

    # Class-level attribute declaration (required for type checkers)
    name: str

    @property
    def is_connected(self) -> bool: ...

    def find_by_id(self, entity_id: str) -> dict[str, Any] | None: ...

    def save(self, entity: dict[str, Any]) -> str: ...

    def delete(self, entity_id: str) -> bool: ...


# =============================================================================
# 6. PROTOCOL WITH BOTH SYNC AND ASYNC METHODS
# =============================================================================
#
# This is fully supported. Just define async methods with `async def`.
# A single Protocol can mix sync and async methods.

class EventBus(Protocol):
    """Protocol mixing sync config with async operations."""

    def configure(self, topic: str) -> None: ...

    async def publish(self, event: dict[str, Any]) -> None: ...

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]: ...


# If you want SEPARATE sync and async variants of the same interface,
# define two Protocols:

class SyncStore(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def save(self, key: str, value: dict[str, Any]) -> None: ...

class AsyncStore(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def save(self, key: str, value: dict[str, Any]) -> None: ...


# =============================================================================
# 7. TESTING PROTOCOLS
# =============================================================================
#
# Three approaches, from best to acceptable:
#
# A) STATIC: Run mypy/pyright -- they verify structural compatibility.
#    This is the BEST approach. If mypy passes, the class implements Protocol.
#
# B) EXPLICIT INHERITANCE (documentation + enforcement):
#    Have the class inherit from the Protocol. This:
#    - Documents intent clearly
#    - Forces mypy to verify compatibility at the class definition site
#    - Does NOT change runtime behavior (no ABC enforcement)
#
# C) RUNTIME isinstance (only with @runtime_checkable):
#    Checks attribute/method PRESENCE only. Use for smoke tests.

# Approach B example -- explicit inheritance for verification:
class MyStore(SyncStore):  # Inheriting from Protocol for documentation
    """mypy will error HERE if any method is missing or has wrong signature."""

    def get(self, key: str) -> dict[str, Any] | None:
        return None

    def save(self, key: str, value: dict[str, Any]) -> None:
        pass


# Approach C -- runtime test with assert:
@runtime_checkable
class Describable(Protocol):
    def describe(self) -> str: ...

def test_protocol_compliance() -> None:
    """Smoke test: verify structural match at runtime."""
    obj = MyConcreteClass()
    assert isinstance(obj, Describable), f"{type(obj).__name__} does not implement Describable"


class MyConcreteClass:
    def describe(self) -> str:
        return "I satisfy Describable"


# =============================================================================
# 8. COMMON MISTAKES AND GOTCHAS
# =============================================================================
#
# GOTCHA 1: Accidental structural match
#   str has .encode() -> bytes. If your Protocol has encode() -> bytes,
#   str will satisfy it. Use specific method names to avoid collisions.
#
# GOTCHA 2: Instance attributes defined in __init__ are invisible to Protocol
#   Protocol attributes MUST be declared at CLASS level.
#
#   WRONG:
#     class Bad(Protocol):
#         def __init__(self) -> None:
#             self.name: str = ""   # mypy ignores this
#
#   RIGHT:
#     class Good(Protocol):
#         name: str                 # class-level declaration
#
# GOTCHA 3: @runtime_checkable does NOT check signatures
#   A class with close(self, x: int) satisfies isinstance(obj, Closeable)
#   even though Closeable.close takes no args. Only mypy catches this.
#
# GOTCHA 4: Protocol methods with default implementations
#   You CAN provide default implementations in Protocol methods.
#   But if a class does NOT inherit from the Protocol, it won't get them.
#   This leads to confusion. Prefer `...` bodies in Protocols.
#
# GOTCHA 5: Properties in Protocols
#   If Protocol defines `@property`, implementing class MUST also use @property.
#   A plain attribute will NOT satisfy a property Protocol member.
#   (However, a read-write attribute DOES satisfy a read-only property in
#   some type checkers -- behavior varies between mypy and pyright.)
#
# GOTCHA 6: ClassVar in Protocol
#   ClassVar members are NOT checked by isinstance() even with @runtime_checkable.
#
# GOTCHA 7: Generic Protocol syntax changed in 3.12
#   Old: class Box(Protocol[T]): ...  (still works)
#   New: class Box[T](Protocol): ...  (preferred in 3.12+)
#
# GOTCHA 8: from __future__ import annotations
#   This makes ALL annotations strings (PEP 563). This is fine for Protocols --
#   mypy resolves them. But @runtime_checkable isinstance checks still work
#   because they check method NAMES, not annotation objects.


# =============================================================================
# 9. PRACTICAL EXAMPLE: STATE STORE PROTOCOL
# =============================================================================

@runtime_checkable
class StateStore(Protocol):
    """Protocol for a generic key-value state store.

    Implementations can be in-memory, Redis, DynamoDB, filesystem, etc.
    """

    def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve state by key. Returns None if not found."""
        ...

    def save(self, key: str, state: dict[str, Any]) -> None:
        """Persist state under the given key."""
        ...

    def list_keys(self, prefix: str = "") -> Sequence[str]:
        """List all keys, optionally filtered by prefix."""
        ...

    def delete(self, key: str) -> bool:
        """Delete state by key. Returns True if existed."""
        ...


# --- Concrete implementation: In-Memory ---

class InMemoryStateStore:
    """Simple dict-backed state store. Satisfies StateStore Protocol."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def save(self, key: str, state: dict[str, Any]) -> None:
        self._data[key] = state

    def list_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]

    def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None


# --- Concrete implementation: Filesystem ---

class FileStateStore:
    """JSON-file-backed state store. Satisfies StateStore Protocol."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def get(self, key: str) -> dict[str, Any] | None:
        import json
        from pathlib import Path
        path = Path(self._base_dir) / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())  # type: ignore[no-any-return]

    def save(self, key: str, state: dict[str, Any]) -> None:
        import json
        from pathlib import Path
        path = Path(self._base_dir) / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2))

    def list_keys(self, prefix: str = "") -> list[str]:
        from pathlib import Path
        base = Path(self._base_dir)
        if not base.exists():
            return []
        return [
            p.stem for p in base.glob("*.json")
            if p.stem.startswith(prefix)
        ]

    def delete(self, key: str) -> bool:
        from pathlib import Path
        path = Path(self._base_dir) / f"{key}.json"
        if path.exists():
            path.unlink()
            return True
        return False


# =============================================================================
# 10. PRACTICAL EXAMPLE: CREDENTIAL PROVIDER PROTOCOL
# =============================================================================

@runtime_checkable
class CredentialProvider(Protocol):
    """Protocol for retrieving credentials (API keys, tokens, etc.).

    Implementations can read from env vars, vaults, config files, etc.
    """

    def get_credential(self, credential_name: str) -> str | None:
        """Return the credential value, or None if not found."""
        ...


# --- Concrete implementation: Environment Variables ---

class EnvCredentialProvider:
    """Reads credentials from environment variables."""

    def __init__(self, prefix: str = "") -> None:
        self._prefix = prefix

    def get_credential(self, credential_name: str) -> str | None:
        import os
        env_key = f"{self._prefix}{credential_name}".upper()
        return os.environ.get(env_key)


# --- Concrete implementation: Dictionary (for testing) ---

class DictCredentialProvider:
    """In-memory credential store. Useful for tests."""

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        self._credentials = credentials or {}

    def get_credential(self, credential_name: str) -> str | None:
        return self._credentials.get(credential_name)


# =============================================================================
# 11. DEPENDENCY INJECTION VIA CONSTRUCTOR PARAMETERS
# =============================================================================
#
# The natural way to use Protocols with DI in Python: accept Protocol types
# as constructor parameters. No framework needed.

@dataclass
class IncidentManager:
    """Service that depends on abstractions (Protocols), not concretions.

    Constructor injection -- the simplest and most explicit DI pattern.
    """
    state_store: StateStore
    credential_provider: CredentialProvider

    def process_incident(self, incident_id: str, data: dict[str, Any]) -> None:
        # Use credential to authenticate
        api_key = self.credential_provider.get_credential("PAGERDUTY_API_KEY")
        if not api_key:
            print(f"[WARN] No API key found, skipping notification")

        # Persist incident state
        self.state_store.save(f"incident:{incident_id}", {
            "status": "processing",
            "data": data,
        })
        print(f"[INFO] Incident {incident_id} saved to state store")

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        return self.state_store.get(f"incident:{incident_id}")


# --- Wiring it together ---

def create_incident_manager_production() -> IncidentManager:
    """Production wiring -- real implementations."""
    return IncidentManager(
        state_store=FileStateStore("/var/lib/aiops/state"),
        credential_provider=EnvCredentialProvider(prefix="AIOPS_"),
    )


def create_incident_manager_testing() -> IncidentManager:
    """Test wiring -- in-memory fakes (NOT mocks)."""
    return IncidentManager(
        state_store=InMemoryStateStore(),
        credential_provider=DictCredentialProvider({
            "PAGERDUTY_API_KEY": "test-key-12345",
        }),
    )


# =============================================================================
# 12. ASYNC PROTOCOL EXAMPLE
# =============================================================================

class AsyncStateStore(Protocol):
    """Async variant of StateStore for use with asyncio."""

    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def save(self, key: str, state: dict[str, Any]) -> None: ...
    async def list_keys(self, prefix: str = "") -> Sequence[str]: ...
    async def delete(self, key: str) -> bool: ...


class InMemoryAsyncStateStore:
    """Async in-memory store. Satisfies AsyncStateStore Protocol."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    async def save(self, key: str, state: dict[str, Any]) -> None:
        self._data[key] = state

    async def list_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]

    async def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None


# =============================================================================
# 13. VERIFICATION PATTERNS -- PUTTING IT ALL TOGETHER
# =============================================================================

def demo() -> None:
    """Run all examples to verify correctness."""

    # --- Greeter ---
    use_greeter(EnglishGreeter())

    # --- runtime_checkable ---
    demo_runtime_checkable()

    # --- StateStore with DI ---
    manager = create_incident_manager_testing()
    manager.process_incident("INC-001", {"severity": "critical", "service": "payment-svc"})
    result = manager.get_incident("INC-001")
    print(f"Retrieved incident: {result}")

    # --- Protocol compliance checks ---
    store = InMemoryStateStore()
    creds = DictCredentialProvider({"KEY": "val"})
    assert isinstance(store, StateStore), "InMemoryStateStore must satisfy StateStore"
    assert isinstance(creds, CredentialProvider), "DictCredentialProvider must satisfy CredentialProvider"

    # --- Verify non-implementing class fails ---
    assert not isinstance("a string", StateStore), "str should not satisfy StateStore"

    print("\nAll Protocol examples passed.")


if __name__ == "__main__":
    demo()
