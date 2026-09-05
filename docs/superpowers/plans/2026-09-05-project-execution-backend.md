# Project Execution Backend Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-project MATLAB/Python selector while preserving MATLAB as the default and persisting the choice across task resume.

**Architecture:** The create-project form sends an `execution_backend` field through the existing multipart endpoint. The validated value is stored on `Problem`, persisted inside the existing workflow checkpoint, and passed explicitly to the interpreter factory; missing values continue to use the global setting for compatibility.

**Tech Stack:** Vue 3, TypeScript, FastAPI, Pydantic v2, Python 3.12, Vitest, pytest/unittest

**Spec:** `docs/superpowers/specs/2026-09-02-project-execution-backend-design.md`

## Global Constraints

- MATLAB remains the default for new projects.
- Supported project values are exactly `matlab` and `python`.
- Python selection must never probe or start MATLAB.
- MATLAB selection preserves the existing Python fallback behavior.
- Old checkpoints and callers without a project selection continue to use `CODE_EXECUTION_BACKEND`.
- A resumed task uses the backend saved in its checkpoint.

---

### Task 1: Interpreter Factory Override

**Files:**
- Modify: `backend/app/tools/interpreter_factory.py`
- Test: `backend/tests/test_matlab_interpreter.py`

**Interfaces:**
- Consumes: `preferred_backend: Literal["matlab", "python"] | None`
- Produces: `create_interpreter(..., preferred_backend=None) -> BaseCodeInterpreter`

- [ ] **Step 1: Write failing factory tests**

Add a test proving `preferred_backend="python"` initializes `LocalCodeInterpreter` and never calls `MatlabCodeInterpreter.initialize`, even while the global setting is MATLAB. Add a second assertion to the existing MATLAB fallback test using an explicit MATLAB preference.

```python
async def test_project_python_override_skips_matlab_probe(self) -> None:
    with patch.object(settings, "CODE_EXECUTION_BACKEND", "matlab"), \
         patch.object(MatlabCodeInterpreter, "initialize", new=AsyncMock()) as matlab_init, \
         patch.object(LocalCodeInterpreter, "initialize", new=AsyncMock()) as python_init:
        interpreter = await create_interpreter(
            task_id="python-project",
            work_dir=tmp,
            notebook_serializer=NotebookSerializer(work_dir=tmp),
            preferred_backend="python",
        )
    self.assertIsInstance(interpreter, LocalCodeInterpreter)
    matlab_init.assert_not_awaited()
    python_init.assert_awaited_once()
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `uv run pytest tests/test_matlab_interpreter.py -q`

Expected: failure because `create_interpreter` does not accept `preferred_backend`.

- [ ] **Step 3: Implement the explicit override**

Add the optional keyword argument and resolve it before the existing validation:

```python
preferred = (preferred_backend or settings.CODE_EXECUTION_BACKEND).strip().lower()
```

Keep the current MATLAB probe/fallback branch and direct Python initialization unchanged after resolution.

- [ ] **Step 4: Run the focused test and commit**

Run: `uv run pytest tests/test_matlab_interpreter.py -q`

Expected: all tests pass; a real MATLAB integration test may be skipped if MATLAB is absent.

Commit: `feat(backend): allow project execution backend override`

---

### Task 2: Persist and Restore the Project Choice

**Files:**
- Modify: `backend/app/schemas/request.py`
- Modify: `backend/app/routers/modeling_router.py`
- Modify: `backend/app/core/workflow.py`
- Test: `backend/tests/test_matlab_interpreter.py`
- Test: `backend/tests/test_workflow_resume.py`

**Interfaces:**
- Consumes: multipart field `execution_backend: Literal["matlab", "python"] | None`
- Produces: `Problem.execution_backend`, serialized into `workflow_state.json`
- Produces: `_schedule_new_task(..., execution_backend=None)` and `run_modeling_task_async(..., execution_backend=None)`

- [ ] **Step 1: Write failing persistence and workflow tests**

Create a `Problem(execution_backend="python")`, initialize `WorkflowCheckpoint`, reload it, and assert:

```python
restored = Problem.model_validate(checkpoint.load()["problem"])
self.assertEqual(restored.execution_backend, "python")
```

Add a workflow unit test that supplies `state["problem"]["execution_backend"] = "python"`, mocks `create_interpreter`, invokes `_initialize_interpreter`, and asserts `preferred_backend="python"`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run pytest tests/test_matlab_interpreter.py tests/test_workflow_resume.py -q`

Expected: schema rejection/missing attribute and missing factory argument.

- [ ] **Step 3: Add the schema and request plumbing**

Define:

```python
ExecutionBackend = Literal["matlab", "python"]

class Problem(BaseModel):
    execution_backend: ExecutionBackend | None = None
```

Accept `execution_backend: ExecutionBackend | None = Form(None)` in `/modeling`. Thread it through `_schedule_new_task` and `run_modeling_task_async` into `Problem`. For example submissions, omit the value so the global MATLAB default remains authoritative.

- [ ] **Step 4: Use the checkpointed value in workflow initialization**

Resolve the saved task configuration without mutating old checkpoints:

```python
problem_data = state.get("problem", {})
preferred_backend = problem_data.get("execution_backend")
self.code_interpreter = await create_interpreter(
    ...,
    preferred_backend=preferred_backend,
)
```

Resume endpoints already restore `Problem` from `state["problem"]`; optional `None` keeps old checkpoints valid.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_matlab_interpreter.py tests/test_workflow_resume.py tests/test_workflow_checkpoint.py -q`

If `test_workflow_checkpoint.py` does not exist, run the first two files plus the full related workflow suite selected by `pytest tests/test_workflow*.py -q`.

Commit: `feat(backend): persist project execution backend`

---

### Task 3: Create-Project Selector and Request Payload

**Files:**
- Modify: `frontend/src/components/UserStepper.vue`
- Modify: `frontend/src/apis/submitModelingApi.ts`
- Create: `frontend/tests/modeling-submission.test.ts`

**Interfaces:**
- Consumes: `ModelingSubmission.execution_backend?: "matlab" | "python"`
- Produces: multipart field `execution_backend`, defaulting to `matlab`

- [ ] **Step 1: Write a failing API serialization test**

Mock the request helper, call `submitModelingTask` with Python, inspect the posted `FormData`, and assert:

```typescript
expect(formData.get("execution_backend")).toBe("python");
```

Also call it without a value and assert `matlab`.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `pnpm test -- tests/modeling-submission.test.ts`

Expected: `execution_backend` is absent.

- [ ] **Step 3: Implement the request type and form field**

Extend the type and fields map:

```typescript
execution_backend?: "matlab" | "python";
// ...
execution_backend: problem.execution_backend ?? "matlab",
```

- [ ] **Step 4: Add the visible selector**

Add `executionBackend = ref<"matlab" | "python">("matlab")` and a “计算环境” Select with labels `MATLAB（默认）` and `Python`. Include `execution_backend: executionBackend.value` in the submission object. Keep the selector separate from language/output options because it controls execution, not paper language.

- [ ] **Step 5: Run frontend verification and commit**

Run:

```bash
pnpm test -- tests/modeling-submission.test.ts
pnpm run check
pnpm run build
```

Expected: all commands pass.

Commit: `feat(frontend): add project execution backend selector`

---

### Task 4: Integrated Verification and Publication

**Files:**
- Modify only if verification reveals a scoped defect in the files above.

**Interfaces:**
- Consumes: completed backend and frontend feature.
- Produces: a pushed branch and reviewable pull request from `codex/python-execution-option` to `main`.

- [ ] **Step 1: Run complete scoped verification**

Run:

```bash
cd backend && uv run ruff check app tests && uv run pytest tests -q
cd ../frontend && pnpm test && pnpm run build
```

- [ ] **Step 2: Audit the final diff**

Verify only the spec, plan, backend request/execution files, frontend selector/API files, and their tests changed. Verify `git diff --check` is clean and author email is `271718778+dujun015-design@users.noreply.github.com`.

- [ ] **Step 3: Push and open a draft pull request**

Push `codex/python-execution-option`, search for an existing matching PR, and create a draft PR only when none exists. The PR must state that MATLAB remains the default, Python is project-scoped, and both backend/frontend verification passed.
