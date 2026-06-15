# Make Browser Pool Queueing Deadline-Aware

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

There is no repository-level `PLANS.md` file checked into this repository. This plan follows the ExecPlan format used by the local `plan-file-author` skill. Keep this document self-contained: a contributor should be able to restart from this file alone.

## Purpose / Big Picture

BR-007 reports that `career:vk` repeatedly fails in general searches with `could not acquire a context within 5000 ms`. A browser context is an isolated Playwright browser session used by a scraper to open pages. The current command-line search creates a shared `BrowserPool` with only two contexts and a fixed five-second wait to acquire one. When several slower browser scrapers run at the same time, later browser sources can fail before they even get a page.

After this change, a browser source should wait its turn for a context within its source attempt deadline instead of failing after a fixed five seconds. A user running a broad search that includes HH-family sources plus `career:vk` and `career:ibs` should see each accessible browser source either return listings, report a real site block such as `blocked/anti_bot_page`, or hit the source attempt deadline. It should not report `pool_acquire_timeout` merely because other browser sources were already using the two available contexts.

## Progress

- [x] (2026-06-12T08:45:28Z) Created branch `codex/br-007-browser-pool-plan` before writing this plan.
- [x] (2026-06-12T08:45:28Z) Read the ExecPlan authoring instructions and inspected the current browser pool, search engine, runtime config, CLI wiring, MCP wiring, and existing browser pool tests.
- [x] (2026-06-12T08:45:28Z) Reproduced the BR-007 symptom on the current code with a broad browser-source search: both `career:vk` and `career:ibs` reported `timeout/pool_acquire_timeout` after two attempts.
- [x] (2026-06-14T10:58:26Z) Revised the plan after review to make `timeout_ms` cover browser/context/page setup, clarify legitimate near-deadline pool acquire timeouts, specify deterministic fake-browser regression tests, and mark the old five-second acquire default as superseded by BR-007.
- [x] (2026-06-14T11:09:23Z) Revised the plan after review to cover cancellation safety during browser rebuild, preserve successful listings when the post-call block probe times out, add an explicit acquire-timeout clamping test, clarify per-attempt `elapsed_ms`, and clamp Playwright page timeouts away from zero.
- [x] (2026-06-14T11:25:08Z) Revised the plan after review to require single-flight browser rebuild serialization, preserve `BlockedResult.partial` listings in `SearchEngine`, and replace an unsupported parser-health claim with a neutral parser-scope boundary.
- [x] (2026-06-14T11:37:38Z) Revised the plan after review to cover rebuild versus shutdown races, bound cleanup independently from the source deadline, and make blocked partial acceptance observable through search artifacts rather than direct `SourceOutcome` access.
- [x] (2026-06-14T11:49:01Z) Revised the plan after review to make old-resource teardown during rebuild a detached tracked cleanup task outside the source deadline, require cleanup closes outside `self._lock`, and replace generic shutdown `RuntimeError` with explicit pool-shutdown handling.
- [x] (2026-06-14T12:11:26Z) Revised the plan after review to define `browser.new_context()` checkout semantics, prevent failed acquisition from decrementing unrelated `in_use` counts, require `shutdown()` to drain tracked cleanup tasks, and define cleanup timeout as a per-batch cap rather than per-resource serial time.
- [x] (2026-06-14T12:21:31Z) Revised the plan after review to require tracked cleanup for locally created but unaccepted `new_context()` contexts and shutdown-loser `new_browser` resources, so outer source-deadline cancellation cannot cancel their cleanup.
- [x] (2026-06-14T12:39:45Z) Implemented deadline-aware context acquisition in `plugins/job-harness/src/job_harness/browser_pool.py`, including single-flight rebuild, explicit `PoolShutdown`, checked-out-only `in_use` accounting, bounded cleanup, and post-call probe timeout preservation.
- [x] (2026-06-14T12:39:45Z) Updated deterministic unit and search-level tests so queued browser-source execution succeeds without site-specific behavior, blocked partial listings are written to raw artifacts, explicit acquire caps remain available, and rebuild/cleanup/shutdown races are covered by fake browsers.
- [x] (2026-06-14T12:41:18Z) Verified the broad browser-source CLI scenario no longer reports an early `pool_acquire_timeout`: `career:ibs` finished `ok` with 5 listings in 12682 ms, and `career:vk` finished `ok` with 6 listings in 14057 ms.
- [ ] (2026-06-14T12:53:35Z) Ran the canonical repository gate from the repository root twice. Ruff, mypy, detect-secrets, all 282 unit tests, MCP smoke, and all live source smokes except `it_jobs_uz` passed; `python3 scripts/verify_repo.py full` still exits 1 because `it_jobs_uz` did not exit within the live smoke's 60 second process limit in both full runs.

## Surprises & Discoveries

- Observation: BR-002 is already merged into `main` as commit `888342f Handle anti-bot blocks in browser and HTTP scrapers`, so this plan starts from a clean `main` that already reports HH anti-bot pages as `blocked`.
  Evidence: `git log --oneline -3` showed `c242bc9 Merge pull request #13 from feodal01/codex/hh-blocked-source-status` and `888342f Handle anti-bot blocks in browser and HTTP scrapers`.

- Observation: BR-007 still reproduces after BR-002. A broad browser-source search on `main` reported `career:vk` as `state: timeout`, `failure_mode: pool_acquire_timeout`, `attempts: 2`, and `error: could not acquire a context within 5000 ms`.
  Evidence: running from `/Users/user/Documents/repos/qa-job-harness`:

        uv --directory plugins/job-harness run job-harness search --query "manual qa" --sources hh_ru,hh_kz,hh_uz,rabota_by,headhunter_kg,career:ibs,career:vk --max-results 5 --format json

  produced source status entries where `career:vk` and `career:ibs` both had `timeout/pool_acquire_timeout`. In the same run, `hh_kz`, `hh_uz`, `rabota_by`, and `headhunter_kg` returned listings, while `hh_ru` reported the expected `blocked/anti_bot_page`.

- Observation: Before BR-007, the fixed five-second acquire timeout was separate from the source attempt timeout. `SourceRuntimeConfig.source_attempt_timeout_ms` defaulted to 30000 ms, but `BrowserPool.acquire_timeout_ms` defaulted to 5000 ms. This meant a source could fail after five seconds even though it still had source budget left.
  Evidence: `plugins/job-harness/src/job_harness/source_runtime.py` defines `source_attempt_timeout_ms: int = 30_000`, while `plugins/job-harness/src/job_harness/browser_pool.py` defines `acquire_timeout_ms: int = 5_000`.

- Observation: The first version of this plan made semaphore waiting and `func(page)` deadline-aware, but did not explicitly cover `BrowserPool._acquire_context_locked()` or `context.new_page()`. Those calls can await lazy browser startup, browser rebuild, `browser_factory()`, `browser.new_context()`, and page creation. Leaving them outside the remaining deadline would preserve hidden unbounded waits.
  Evidence: `plugins/job-harness/src/job_harness/browser_pool.py` currently does `context = await self._acquire_context_locked()` and `page = await context.new_page()` after semaphore acquisition and before wrapping `func(page)` in `asyncio.wait_for(...)`.

- Observation: Older checked-in plans document `BrowserPool.acquire_timeout_ms=5_000` as a default to keep, but BR-007 shows that fixed default now causes false source failures in normal broad browser searches.
  Evidence: `plans/search-layer-architecture.plan.md` says to keep `BrowserPool.__init__` defaults including `acquire_timeout_ms=5_000`; `plans/resilient-scraping.md` also lists `BrowserPool.acquire_timeout_ms` as 5000 ms. This ExecPlan intentionally supersedes that part of those older plans.

- Observation: Putting a `wait_for` deadline around `BrowserPool._acquire_context_locked()` also puts cancellation pressure on `_rebuild_browser_locked()`. The current rebuild closes idle contexts, clears the context list, closes the browser, sets the browser to `None`, increments rebuild count, and awaits `browser_factory()` across several await points. A cancellation in the middle can leave shared pool state between old and new browser generations unless the rebuild is made cancellation-safe.
  Evidence: `plugins/job-harness/src/job_harness/browser_pool.py` currently performs those awaits inside `_rebuild_browser_locked()` while mutating `self._state`.

- Observation: `is_blocked(page)` currently runs after `func(page)` has already returned listings. If a new remaining-deadline wrapper raises `TimeoutError` during that post-call probe, `SearchEngine._run_browser_source` maps the whole attempt to `goto_timeout` and loses the listings because it only assigns listings when `run_with_page` returns a list.
  Evidence: `plugins/job-harness/src/job_harness/browser_pool.py` calls `value = await asyncio.wait_for(func(page), ...)` and then `block = await is_blocked(page)`. `plugins/job-harness/src/job_harness/search_engine.py` catches `TimeoutError` before reading any returned listings.

- Observation: Browser source `elapsed_ms` is measured per attempt, not across all attempts plus retry sleep. Retry backoff happens in `_run_source` outside `_run_browser_source`.
  Evidence: `plugins/job-harness/src/job_harness/search_engine.py` sets `started_at` inside `_run_browser_source` and computes `elapsed_ms` before returning that attempt's `SourceOutcome`; `_run_source` sleeps for retry backoff after the attempt outcome is created.

- Observation: A cancellation-safe rebuild that moves slow awaits outside `self._lock` needs explicit single-flight serialization. Otherwise several concurrent acquire calls can observe `self._state.browser is None` and launch several `browser_factory()` calls. A helper that tries to acquire `self._lock` while `_acquire_context_locked()` already holds it would deadlock because `asyncio.Lock` is not reentrant.
  Evidence: `plugins/job-harness/src/job_harness/browser_pool.py` currently calls `await self._rebuild_browser_locked()` while inside `async with self._lock`.

- Observation: `BlockedResult.partial` is currently not copied into browser source listings. If `run_with_page` returns `BlockedResult(block=..., partial=value)`, `SearchEngine._run_browser_source` sets `state=BLOCKED` and error metadata but leaves `listings` empty.
  Evidence: in `plugins/job-harness/src/job_harness/search_engine.py`, the `isinstance(result, BlockedResult)` branch does not assign `listings = result.partial`.

- Observation: Moving browser rebuild factory work outside `self._lock` creates a shutdown race unless the commit step checks `self._state.shutting_down`. `BrowserPool.shutdown()` currently sets `shutting_down` and clears `self._state.browser` under `self._lock`, but it does not coordinate with a separate rebuild lock.
  Evidence: `plugins/job-harness/src/job_harness/browser_pool.py` sets `self._state.shutting_down = True` in `shutdown()` under `self._lock`; the proposed rebuild refactor awaits `self._browser_factory()` outside that lock.

- Observation: Cleanup is not fully bounded today. `page.close()` has a separate 3 second `wait_for`, but `_release_context_locked()` calls `context.close()` without a timeout when a context is poisoned or the pool is shutting down. A hanging poisoned context close can make `run_with_page(timeout_ms=100)` wait far past the source deadline.
  Evidence: `plugins/job-harness/src/job_harness/browser_pool.py` wraps `page.close()` with `timeout=3.0`, while the poisoned `context.close()` call in `_release_context_locked()` is awaited directly.

- Observation: The previous plan text mixed two incompatible contracts for old-resource teardown during rebuild. It said `_acquire_context_locked()` should be wrapped in the source deadline, but also said old context and browser closes during rebuild are cleanup outside the source deadline. If old-resource close awaits run inside `_acquire_context_locked()`, the outer `asyncio.wait_for(..., timeout=remaining)` can cancel those closes.
  Evidence: an earlier revision of this ExecPlan put old-resource close work before `browser_factory()` inside the acquisition path, while also defining those closes as cleanup.

- Observation: Cleanup currently runs under `self._lock` in `_release_context_locked()`. Even if bounded with `wait_for`, awaiting `context.close()` while holding the lock can block `health()`, `shutdown()`, and other acquire/release calls for the cleanup cap.
  Evidence: `plugins/job-harness/src/job_harness/browser_pool.py` awaits poisoned `context.close()` inside `async with self._lock`.

- Observation: A generic `RuntimeError("pool is shutting down")` from browser acquisition would be misclassified by the search engine if it reached `_run_browser_source`.
  Evidence: `plugins/job-harness/src/job_harness/search_engine.py` catches generic `Exception` after timeout and pool-acquire cases and maps it to `FailureMode.PARSE_ERROR`.

- Observation: `browser.new_context()` currently awaits under `self._lock`, and `in_use` is incremented only after the await returns. If that await times out or raises before a context is issued, the `finally` path can still call `_release_context_locked(None)`, which decrements `in_use` even though this caller never checked out a context.
  Evidence: `plugins/job-harness/src/job_harness/browser_pool.py` awaits `self._state.browser.new_context(...)` before `self._state.in_use += 1`, while `_release_context_locked(None)` decrements `in_use`. A failed second acquire can therefore make health show fewer in-use contexts than are actually checked out.

- Observation: Tracked cleanup tasks must be part of `shutdown()` because the CLI awaits `pool.shutdown()` before the event loop ends. If shutdown returns while background cleanup tasks are still pending, those tasks can be destroyed pending when the CLI event loop closes.
  Evidence: `plugins/job-harness/src/job_harness/cli.py` awaits `pool.shutdown()` in `finally`, then leaves the async run; background tasks not awaited by shutdown would no longer have a stable owner.

- Observation: Cleanup timeout language must define whether the cap applies once per cleanup batch or once per resource. If two old contexts and one old browser close sequentially with a per-resource cap, total teardown can take three times the cleanup timeout.
  Evidence: this plan now uses tracked cleanup tasks for old browser-generation resources, so the cleanup batch can include multiple closeable resources.

- Observation: Locally created resources that are rejected before checkout have the same cancellation risk as old browser-generation cleanup. If a returned `new_context()` is rejected because shutdown started or the browser generation changed, or if a `new_browser` returned after shutdown wins a rebuild race, inline close work inside the acquisition function can still be cancelled by the outer source-deadline `wait_for`.
  Evidence: this plan wraps context acquisition in `asyncio.wait_for(..., timeout=remaining)`, so cleanup inside that acquisition function remains cancelable unless it is shielded or moved to tracked cleanup.

- Observation: The BR-007 live CLI scenario passes after the browser pool change: `career:ibs` and `career:vk` both finished `ok` with no `pool_acquire_timeout`.
  Evidence: running `uv --directory plugins/job-harness run job-harness search --query "manual qa" --sources hh_ru,hh_kz,hh_uz,rabota_by,headhunter_kg,career:ibs,career:vk --max-results 5 --format json` produced `career:ibs` with `listings_written=5`, `elapsed_ms=12682`, and `career:vk` with `listings_written=6`, `elapsed_ms=14057`.

- Observation: The full repository gate is currently blocked by an unrelated live HTTP source timeout, not by the BR-007 browser-pool path.
  Evidence: `python3 scripts/verify_repo.py full` was run twice. In both runs Ruff, mypy, detect-secrets, all 282 unit tests, MCP smoke, and browser live smokes including `career:ibs` and `career:vk` passed. The only full-gate failure was `it_jobs_uz: process did not exit within 60s`. Running the same `it_jobs_uz` CLI command separately exited 0 with a source-level `http_timeout` after two attempts and `elapsed_ms=30001`.

## Decision Log

- Decision: Keep browser scraping bounded instead of fully parallelizing every browser source.
  Rationale: Browser scraping competes for one IP address, one VPN path, memory, CPU, Playwright browser process capacity, and anti-bot reputation. Fully opening every browser source at once would increase blocking and resource contention. Bounded parallelism with fair queueing is more reliable: every source gets a turn without overwhelming the environment.
  Date/Author: 2026-06-12 / Codex

- Decision: Replace the fixed default acquire timeout with deadline-aware queueing.
  Rationale: The source attempt deadline is already the system's unit of work budget. Waiting for a context and running the scraper should share that budget. A small fixed acquire timeout makes queue position look like source failure.
  Date/Author: 2026-06-12 / Codex

- Decision: Treat BR-007 as a superseding change to the older `acquire_timeout_ms=5_000` plan contract.
  Rationale: The older plans were written before repeated `pool_acquire_timeout` failures were observed in normal broad searches. The new contract is that `timeout_ms` is the total browser call budget and the default browser pool acquire behavior is deadline-aware. Implementation must update neighboring plan documentation that still claims `acquire_timeout_ms=5_000` is the desired default.
  Date/Author: 2026-06-14 / Codex

- Decision: Preserve explicit small acquire timeouts for tests and emergency infrastructure guards.
  Rationale: A caller should still be able to configure `BrowserPool(acquire_timeout_ms=200)` to prove the pool surfaces saturation. The production default should not be five seconds. This keeps the existing failure mode available for true pool degradation while removing it from normal broad searches.
  Date/Author: 2026-06-12 / Codex

- Decision: Do not mark a source `partial` when it never obtained a browser context.
  Rationale: In this repository, `partial` means a source collected some data but did not finish. A source that never opened a page has no partial evidence. With deadline-aware queueing, the expected normal outcome is success after waiting; if the entire attempt budget expires before a context is available, `timeout/pool_acquire_timeout` is still correct.
  Date/Author: 2026-06-12 / Codex

- Decision: Preserve distinct timeout classification by operation phase.
  Rationale: `SearchEngine._run_browser_source` already maps `PoolAcquireTimeout` to `failure_mode=pool_acquire_timeout` and generic `TimeoutError` from browser page work to `failure_mode=goto_timeout`. The implementation should keep that distinction. Semaphore wait timeout before any context is acquired is `PoolAcquireTimeout`. Timeout while building or rebuilding the browser, acquiring a context object, opening a page, or running the scraper callable is `TimeoutError`. If `new_page()` times out after a context was acquired, the context should be treated as poisoned so it is closed and not reused.
  Date/Author: 2026-06-14 / Codex

- Decision: Make browser rebuild cancellation-safe before putting it under a per-call deadline.
  Rationale: The shared pool state must remain coherent even when `asyncio.wait_for` cancels context acquisition during browser rebuild. The implementation should detach old contexts and the old browser from `self._state` before awaits, schedule slow old-resource close work in tracked cleanup tasks, await only the new browser factory in the acquisition path, and commit the new browser back to state only after the awaited factory succeeds. If the factory is cancelled, the pool should remain in a valid state with no idle contexts from the old generation and `browser=None`, so the next acquire can retry a rebuild while detached cleanup continues independently.
  Date/Author: 2026-06-14 / Codex

- Decision: A successful scraper result must survive a timeout in the post-call block probe.
  Rationale: The anti-bot probe is a classification enhancement after the scraper has already produced data. If `func(page)` returned listings and the remaining budget is exhausted before or during `is_blocked(page)`, `run_with_page` should return the successful value rather than raising `TimeoutError` and causing `SearchEngine` to discard listings as `goto_timeout`. A fast block signal may still return `BlockedResult(block=..., partial=value)`.
  Date/Author: 2026-06-14 / Codex

- Decision: Serialize browser rebuilds with an explicit single-flight mechanism.
  Rationale: The rebuild path should be cancellation-safe without allowing concurrent factories or deadlocking on `self._lock`. Add a separate rebuild lock or single shared rebuild task, and make the rebuild helper's locking contract explicit. The preferred implementation is an `asyncio.Lock` such as `self._rebuild_lock`: `_acquire_context_locked()` checks whether rebuild is needed under `self._lock`, releases `self._lock`, enters `self._rebuild_lock`, rechecks whether rebuild is still needed, detaches old resources from state in a short `self._lock` section, schedules old-resource cleanup in tracked bounded tasks, awaits the new browser factory outside `self._lock`, and commits the new browser under `self._lock` only after the factory succeeds.
  Date/Author: 2026-06-14 / Codex

- Decision: Preserve `BlockedResult.partial` listings in `SearchEngine`.
  Rationale: Once the scraper callable has returned listings, a later anti-bot signal should not erase that evidence. If `run_with_page` returns `BlockedResult` with `partial` containing `list[RawListing]`, `_run_browser_source` should keep `state=BLOCKED` and the block failure mode while also assigning those listings so raw evidence and source summaries reflect what was collected before the block signal.
  Date/Author: 2026-06-14 / Codex

- Decision: A rebuild must not commit a new browser after shutdown begins.
  Rationale: `shutdown()` is the pool owner saying no new browser resources should remain. If a gated or slow `browser_factory()` returns after `shutdown()` has set `self._state.shutting_down`, rebuild should schedule the locally created browser into a tracked bounded cleanup task and raise a shutdown/cancellation result instead of assigning it to `self._state.browser`. It must not await `new_browser.close()` inline inside the source-deadline acquisition function, because the outer `asyncio.wait_for` could cancel that cleanup.
  Date/Author: 2026-06-14 / Codex

- Decision: Cleanup is outside the source attempt budget but must always be bounded.
  Rationale: Cleanup after a result or error is resource hygiene, not scraper work. It should not turn a successful listing result into a source timeout merely because the source budget is exhausted. At the same time, cleanup must not hang forever. Use a small independent cleanup timeout for `page.close()`, poisoned `context.close()`, and local browser closes during shutdown/rebuild races. The expected wall-clock duration of `run_with_page(timeout_ms=100)` may exceed 100 ms by at most the cleanup cap, but it must not hang indefinitely.
  Date/Author: 2026-06-14 / Codex

- Decision: Old browser resources detached during rebuild are closed by tracked cleanup tasks, not awaited inside the source-deadline acquisition path.
  Rationale: Rebuild has two different kinds of work. Creating the new browser and context determines whether the source can run and belongs inside `timeout_ms`. Tearing down the old browser generation is cleanup and should not consume or be cancelled by the source deadline. Detach old resources from shared state under `self._lock`, schedule their bounded cleanup in tracked tasks, and continue acquisition. The cleanup task must remove itself from the tracked task set when done so tests can assert there are no pending cleanup tasks after the cleanup cap.
  Date/Author: 2026-06-14 / Codex

- Decision: Cleanup closes must not run while holding `self._lock`.
  Rationale: The pool lock protects shared state, not slow Playwright close operations. Detach or update pool state under the lock, then close pages, contexts, and browsers outside the lock with the bounded cleanup helper. This prevents a stuck close from blocking `health()`, `shutdown()`, acquire, or release for the cleanup cap.
  Date/Author: 2026-06-14 / Codex

- Decision: Pool shutdown during browser acquisition must not surface as `parse_error`.
  Rationale: Shutdown is lifecycle control, not a scraper parser failure. Introduce a specific `PoolShutdown` exception or equivalent shutdown signal from `BrowserPool`, and update `SearchEngine._run_browser_source` to catch it before generic `Exception`. Map it to `SourceState.CANCELLED` and `FailureMode.USER_CANCELLED`, or re-raise `asyncio.CancelledError` if the whole search is already being cancelled. Do not let a generic `RuntimeError("pool is shutting down")` reach the generic exception handler.
  Date/Author: 2026-06-14 / Codex

- Decision: Treat browser contexts as checked out only after `browser.new_context()` returns and the pool accepts the context under `self._lock`.
  Rationale: `in_use` must count contexts actually issued to callers. A failed or timed-out `new_context()` did not issue a context and must not trigger `_release_context_locked(None)` or decrement `in_use`. Create new contexts outside `self._lock`, then reacquire `self._lock` to verify the pool is not shutting down and the local browser is still current. Increment `in_use` only when accepting that context as checked out.
  Date/Author: 2026-06-14 / Codex

- Decision: `shutdown()` owns tracked cleanup tasks before returning.
  Rationale: The pool should not leave background cleanup tasks for the CLI event loop to destroy after `await pool.shutdown()`. Shutdown should set `shutting_down`, detach current resources and snapshot `self._cleanup_tasks` under `self._lock`, then await the detached resources and pending cleanup tasks with the bounded cleanup helper. When shutdown returns, no cleanup task owned by the pool should remain pending and the task set should be empty.
  Date/Author: 2026-06-14 / Codex

- Decision: Cleanup timeout is a per-batch wall-clock cap, not a per-resource serial multiplier.
  Rationale: A cleanup batch can contain several old contexts plus a browser. Waiting `cleanup_timeout_s` for each resource sequentially makes total shutdown/rebuild cleanup scale with resource count. Instead, start close operations for a batch concurrently and enforce one `cleanup_timeout_s` wall-clock cap for that batch. Pending close operations are cancelled or abandoned after the cap, exceptions are observed, and pool state no longer retains those resources.
  Date/Author: 2026-06-14 / Codex

- Decision: Locally created but unaccepted resources are cleaned through the tracked cleanup path.
  Rationale: A `new_context()` result that loses a shutdown or generation race was never checked out, but it is still a real Playwright resource. A `new_browser` returned after shutdown wins a rebuild race is the same. Closing those resources inline inside the acquisition function leaves cleanup cancelable by the outer source-deadline `wait_for`. Instead, schedule them into the same tracked bounded cleanup system used for old browser-generation resources, or shield the bounded cleanup task and track it until completion. Acquisition can then raise `PoolShutdown`, retry, or time out without leaking locally created resources.
  Date/Author: 2026-06-14 / Codex

## Outcomes & Retrospective

Implemented deadline-aware browser context queueing. `BrowserPool.run_with_page(..., timeout_ms=...)` now treats `timeout_ms` as the total source-result budget for semaphore acquisition, lazy browser creation/rebuild, context creation, page creation, scraper work, and the post-call block probe. The production default no longer has a fixed five-second acquire cap; explicit `acquire_timeout_ms` remains available for tests and emergency guardrails.

The deterministic regression suite now covers deadline-aware queueing, explicit acquire caps, checked-out-only `in_use` accounting, stale/shutdown-loser context cleanup, single-flight rebuild, rebuild timeout recovery, shutdown during rebuild, bounded cleanup, cleanup task draining, post-call block-probe timeout preservation, search-level browser queueing, and blocked partial raw artifact writes.

The reproduced BR-007 CLI scenario passed: `career:ibs` and `career:vk` both finished `ok` with listings and no `pool_acquire_timeout`. The repository `default` gate passed. The `full` gate was attempted twice and is not marked complete because the external live smoke for `it_jobs_uz` exceeded its 60 second process limit both times; all static checks, secrets, unit tests, MCP smoke, and other live source smokes passed.

## Context and Orientation

The repository root is `/Users/user/Documents/repos/qa-job-harness`. The plugin runtime lives under `plugins/job-harness`.

The search engine starts one task per eligible source in `plugins/job-harness/src/job_harness/search_engine.py`. HTTP sources run through `HttpRunner`. Browser sources run through `SearchEngine._run_browser_source`, which calls `self._browser_pool.run_with_page(_callable, timeout_ms=timeout_ms)`. In this call, `timeout_ms` is the source attempt timeout, usually 30000 ms.

`BrowserPool` is implemented in `plugins/job-harness/src/job_harness/browser_pool.py`. It owns a Playwright browser and a semaphore. A semaphore is a counter that limits how many callers can use a resource at once. Here, `max_contexts=2` means only two browser pages can be active at the same time. Extra browser sources wait for the semaphore before opening a page.

The original bug came from the gap between two timeouts in `BrowserPool.run_with_page`. Before BR-007, it waited for a semaphore slot with `acquire_timeout_ms`, which defaulted to 5000 ms. Only after acquiring a slot did it run the scraper callable with `timeout_ms`. The result was that a queued source could fail after five seconds even though its source attempt budget was thirty seconds.

`SourceRuntimeConfig` in `plugins/job-harness/src/job_harness/source_runtime.py` defines the source attempt timeout and retry policy. `SOURCE_LEVEL_RETRYABLE_FAILURES` includes `FailureMode.POOL_ACQUIRE_TIMEOUT`, so the engine already retries this condition once by default. The original BR-007 reproduction showed the retry did not solve the problem when both attempts hit the same five-second queue limit.

The CLI creates a pool in `plugins/job-harness/src/job_harness/cli.py` with `BrowserPool(max_contexts=2)`. The MCP server creates a singleton pool in `plugins/job-harness/scripts/mcp-server.py` with `BrowserPool(max_contexts=2)`. Because `BrowserPool` now defaults `acquire_timeout_ms` to `None`, both call sites inherit deadline-aware queueing without duplicating policy.

Two older design plans mention the five-second acquire timeout. `plans/search-layer-architecture.plan.md` describes the source runtime contract and says the existing `BrowserPool.__init__` defaults, including `acquire_timeout_ms=5_000`, should be kept. `plans/resilient-scraping.md` lists the same default in its timeout table and BrowserPool sketch. This ExecPlan supersedes those lines for BR-007. When implementing this plan, update those two documents so repository documentation no longer gives conflicting instructions.

Existing tests for the pool are in `plugins/job-harness/tests/test_browser_pool.py`. Existing search engine orchestration tests are in `plugins/job-harness/tests/test_search_engine.py`. Fake browser classes used by tests are in `plugins/job-harness/tests/_support/fake_browser.py`.

## Milestones

The first milestone is to change only the shared browser pool deadline semantics. At the end of this milestone, `plugins/job-harness/src/job_harness/browser_pool.py` should treat `timeout_ms` as the total budget for semaphore wait, new browser/context setup, page creation, scraper execution, and the post-call block probe, while keeping browser rebuild cancellation-safe and single-flight. Single-flight means that when several callers all notice that the browser needs rebuilding, exactly one rebuild operation runs and the other callers wait for that same rebuild result instead of launching their own factories. Cleanup after result or error is deliberately outside the source deadline, but every cleanup batch must have a small independent wall-clock cap so it cannot hang the source forever. Old browser-generation cleanup during rebuild and locally created but unaccepted resources are detached from shared state and closed by tracked cleanup tasks, so source-deadline cancellation cannot cancel their cleanup. Browser context creation should run outside `self._lock`, and `in_use` should count only contexts that were actually checked out to a caller. The focused proof is that `plugins/job-harness/tests/test_browser_pool.py` still passes, including one test where an explicit small acquire timeout raises quickly, one test where an explicit large acquire timeout is capped by the smaller per-call deadline, one test where the default deadline-aware queue lets a waiting caller complete, one test where failed `browser.new_context()` does not decrement another caller's in-use context, one test where a rejected `new_context()` context is cleaned by tracked cleanup even if acquisition is timing out, one test where cancellation during rebuild leaves the pool usable, one test where concurrent acquire calls trigger only one browser factory during rebuild, one test where shutdown during a gated rebuild does not leave a browser committed to pool state, one test where shutdown drains tracked cleanup tasks, one test where old idle context/browser cleanup continues to the cleanup cap after an acquire timeout during rebuild, one test where poisoned context cleanup cannot hang indefinitely, and one test where a post-call block-probe timeout returns the already successful scraper value.

The second milestone is to prove the fix at the search engine layer and update nearby documentation. At the end of this milestone, `plugins/job-harness/tests/test_search_engine.py` or a nearby dispatch test should contain three fake browser sources that contend for two contexts and still complete without `FailureMode.POOL_ACQUIRE_TIMEOUT`. The same test file should prove blocked partial listings through observable search artifacts: a blocked source status, `listings_written=1` for that source or in the raw-search summary, and one raw listing written to `raw_search.jsonl`. `plans/search-layer-architecture.plan.md` and `plans/resilient-scraping.md` should no longer state that `acquire_timeout_ms=5_000` is the desired default.

The third milestone is to verify the original BR-007 scenario and the repository gate. At the end of this milestone, the broad CLI search should no longer show `career:vk` or `career:ibs` failing around 5000 ms with the old acquire timeout message, and `python3 scripts/verify_repo.py full` should pass from the repository root.

## Plan of Work

Start by updating `BrowserPool` so `acquire_timeout_ms` is optional. The constructor in `plugins/job-harness/src/job_harness/browser_pool.py` should accept `acquire_timeout_ms: int | None = None`. When `acquire_timeout_ms` is `None`, `run_with_page` should wait for the semaphore until the per-call `timeout_ms` deadline is exhausted. When `acquire_timeout_ms` is an integer, keep the existing explicit cap behavior, but never wait beyond the per-call deadline.

Inside `BrowserPool.run_with_page`, compute a monotonic deadline at the beginning of the call. Monotonic time is a clock for measuring elapsed time that is not affected by wall-clock changes. Use this deadline for every awaited operation that determines the source result: semaphore wait, new browser/context setup, page creation, scraper callable execution, and the post-call anti-bot probe if it awaits page state. Cleanup after a result or exception uses a separate cleanup timeout described below. Do not leave new browser creation, `browser.new_context()`, or `context.new_page()` outside the source-result budget. Old-resource cleanup from a previous browser generation is not source-result work and must not be awaited inside this budget. The sequence should be:

1. Resolve `timeout_s` from the `timeout_ms` argument or from `self._page_timeout_ms`.
2. Set `deadline = monotonic() + timeout_s`.
3. Compute how long to wait for the semaphore. If `self._acquire_timeout_ms` is `None`, wait for the remaining call budget. If it is set, wait for the smaller of the configured acquire timeout and the remaining call budget.
4. Acquire the semaphore or raise `PoolAcquireTimeout` if the acquire wait expires.
5. Before or while adding the deadline wrapper, refactor browser rebuild so it is cancellation-safe, shutdown-safe, single-flight, and clear about old-resource cleanup. Do not call a helper that tries to acquire `self._lock` while `_acquire_context_locked()` already holds `self._lock`; that would deadlock because `asyncio.Lock` is not reentrant. The preferred shape is to add a separate `self._rebuild_lock = asyncio.Lock()` in `BrowserPool.__init__` and a tracked cleanup-task set such as `self._cleanup_tasks: set[asyncio.Task[None]]`. `_acquire_context_locked()` should use `self._lock` only to check `shutting_down` and whether rebuild is needed, then release `self._lock` before awaiting the rebuild lock. Inside `async with self._rebuild_lock`, recheck under `self._lock` whether another caller already rebuilt the browser or whether shutdown has started. If rebuild is still needed, detach old idle contexts and the old browser into local variables under `self._lock`, clear `self._state.contexts`, set `self._state.browser = None`, and update rebuild counters. Immediately schedule old idle context and old browser closes through a tracked cleanup task that uses the bounded cleanup helper, and do not await that old-resource cleanup inside `_acquire_context_locked()`. Then await `self._browser_factory()` outside `self._lock` but while holding `self._rebuild_lock`, so other rebuilders wait instead of starting another factory. Before committing a returned `new_browser`, reacquire `self._lock` and check `self._state.shutting_down`. If shutdown started while the factory awaited, do not assign `new_browser` to `self._state.browser`; schedule `new_browser` into tracked bounded cleanup and raise a specific `PoolShutdown` exception or equivalent shutdown signal. Commit `self._state.browser = new_browser` and reset `consecutive_hangs` under `self._lock` only if shutdown has not started. If cancellation or timeout happens during factory startup, the pool should remain coherent with no old idle contexts and no current browser, so a later acquire can retry the rebuild while detached cleanup tasks finish independently.
6. Refactor new context creation so `browser.new_context()` is awaited outside `self._lock` but still under the source-result deadline. The safe shape is: under `self._lock`, verify `shutting_down` is false, reuse and check out an idle context if available, or copy the current browser object into a local variable. Release `self._lock`, await `browser.new_context(**self._context_kwargs)` with the remaining deadline, then reacquire `self._lock`. If shutdown has started, or if `self._state.browser` is no longer the same browser object used to create the context, do not increment `in_use`; schedule the newly created context into tracked bounded cleanup and either raise `PoolShutdown` for shutdown or retry acquisition while budget remains for a stale browser generation. Do not await that rejected-context cleanup inline inside the acquisition function, because the acquisition function is wrapped by the source-deadline `wait_for`. Only increment `self._state.in_use` after a context actually exists and the pool accepts it as checked out. A timed-out or failed `browser.new_context()` before checkout must not call `_release_context_locked(None)` and must not decrement `in_use`.
7. Run `self._acquire_context_locked()` or its renamed replacement under `asyncio.wait_for(..., timeout=remaining)` so lazy browser startup, browser rebuild decision-making, `browser_factory()`, and accepted `browser.new_context()` are inside the same source-result deadline. The old-resource cleanup task spawned by rebuild must not be awaited by this call and therefore must not be cancelled by this outer `wait_for`. If context acquisition times out after the semaphore was acquired but before a context was checked out, let the resulting `TimeoutError` propagate so `SearchEngine` maps it to `goto_timeout`, and release only the semaphore. Do not release a context or decrement `in_use` unless this call actually checked out a context.
8. Run `context.new_page()` under `asyncio.wait_for(..., timeout=remaining)`. If page creation times out or raises after a context was checked out, mark the context poisoned in the cleanup path so it is closed instead of returned to the idle pool.
9. Set the page default timeout to the smaller of `self._page_timeout_ms` and the remaining call budget in milliseconds. If the remaining budget is already less than or equal to zero, raise `TimeoutError` before calling Playwright. Otherwise clamp the value to at least 1 ms, for example `max(1, min(self._page_timeout_ms, int(remaining * 1000)))`. Do not pass 0 to Playwright because Playwright treats 0 as disabling timeouts.
10. Run `func(page)` with only the remaining budget.
11. Run `is_blocked(page)` with only the remaining budget, but do not let this post-call probe discard a successful `func(page)` return value. Once `func(page)` returns `value`, that value is durable. If there is no remaining time for the probe, return `value`. If the bounded probe times out, return `value`. If the probe returns a block quickly, return `BlockedResult(block=block, partial=value)` as today. A timeout in this probe should not increment the hang streak or surface to `SearchEngine` as `goto_timeout`.
12. Preserve existing handling for `BrowserBlocked`, `TimeoutError`, context cleanup, poisoned context handling, hang streak tracking, and anti-bot probing.

Cleanup has its own bounded contract. It is outside the source result deadline because cleanup should not convert a successful result into a timeout after the result exists, and old browser-generation cleanup or rejected local resource cleanup should not be cancelled by a source-deadline timeout during acquisition. It must still be bounded so cleanup cannot hang the caller indefinitely. Keep or introduce a small cleanup timeout, for example `cleanup_timeout_s = 3.0`, and apply it as a per-batch wall-clock cap, not as `cleanup_timeout_s` multiplied by the number of resources. A cleanup batch containing several old contexts plus one browser should start all close operations concurrently, wait at most one cleanup timeout for the whole batch, then cancel or abandon pending close operations while observing exceptions. Use this bounded cleanup batch helper for every cleanup close that can await: `page.close()`, poisoned `context.close()`, rejected `new_context()` contexts, context close during `shutdown()`, old context/browser close during rebuild, and locally created `new_browser.close()` when shutdown wins the rebuild race. For all context and browser cleanup, detach or update shared state under `self._lock`, then perform `close()` outside `self._lock` with the bounded cleanup helper. For old resources detached during rebuild and local resources created but not accepted into pool state, schedule a tracked cleanup task and do not await it inside `_acquire_context_locked()` or any acquisition function wrapped by source `wait_for`. If a caller wants to wait for such cleanup, it must use `asyncio.shield` around the bounded cleanup task and still register that task in `self._cleanup_tasks`; the simpler preferred approach is to schedule and return/raise without inline waiting. Track cleanup tasks in a set, remove each task when it completes, and observe or suppress expected close exceptions inside the task so there are no unhandled task warnings. `shutdown()` must set `shutting_down`, detach current idle resources, snapshot `self._cleanup_tasks`, and then bounded-await the detached resources and pending cleanup tasks outside `self._lock`; when `shutdown()` returns, the cleanup task set should be empty and no pool-owned cleanup task should remain pending. If a cleanup close times out or raises, mark the context poisoned, do not return it to the idle pool, and continue releasing the semaphore. A `run_with_page(timeout_ms=100)` call may take up to roughly `100 ms + cleanup_timeout_s` when cleanup after that same call hangs, but it must not remain pending forever. A rebuild acquire may return or time out while old-generation cleanup continues in its tracked task; after one cleanup cap for that batch, tests should observe no pending cleanup task and no old-resource references retained by pool state.

The cleanup path must remain safe. The semaphore should be released only if it was acquired. In the current code, the semaphore is always released in `finally` because the function raises before entering the `try` block when acquire fails. Keep that invariant clear after editing. If the implementation moves the acquire inside a broader `try`, add an `acquired` boolean so a failed acquire does not release a semaphore it never obtained.

Update the class docstring and `PoolAcquireTimeout` message so the behavior is understandable. The message can remain `could not acquire a context within ... ms` when an explicit acquire timeout is configured. When the timeout comes from the source attempt deadline, use a message such as `could not acquire a context before the source deadline`. For timeouts after the semaphore is acquired, raise or allow `TimeoutError`, not `PoolAcquireTimeout`, so the source summary says the browser/page setup exceeded the source deadline rather than falsely saying the queue never produced a context.

Add or update tests in `plugins/job-harness/tests/test_browser_pool.py`. Keep the existing `test_acquire_timeout_when_pool_saturated` by passing `acquire_timeout_ms=200`; it should still raise `PoolAcquireTimeout` quickly. Add a test proving explicit acquire timeout is capped by the per-call deadline: start one task that holds the only context, create `BrowserPool(max_contexts=1, acquire_timeout_ms=5000, ...)`, start a second task with `timeout_ms=100`, and assert it raises `PoolAcquireTimeout` around the 100 ms source deadline rather than waiting anywhere near five seconds. Add a new test for deadline-aware queueing with `BrowserPool(max_contexts=1, acquire_timeout_ms=None, ...)`: start one task that holds the only context briefly, start a second task with enough `timeout_ms` to wait, and assert the second task succeeds after the first releases the context. This test should prove that `acquire_timeout_ms=None` means queueing is bounded by the per-call deadline instead of a fixed pool timeout.

Add `browser.new_context()` checkout tests. Start one task that successfully checks out a context and holds a page. Then make the fake browser's next `new_context()` call hang or raise, and run a second `run_with_page(timeout_ms=100)` that times out or fails before context checkout. Assert `BrowserPool.health().contexts_in_use` still reports the first active context, not zero. Add a shutdown race variant where `new_context()` returns a context after shutdown starts; assert that the returned context is scheduled into tracked cleanup, `in_use` is not incremented, the second caller reports `PoolShutdown` or cancelled status, and the first active context count is not decremented by that failed acquisition. Add a cancellation variant where `new_context()` returns a context, the pool rejects it because the browser generation changed, and the acquisition deadline expires immediately after rejection. Assert rejected-context cleanup still completes through the tracked cleanup task after the source attempt has returned or timed out. These tests prove that `in_use` changes only for contexts actually accepted as checked out and that rejected local contexts are not leaked when the outer source deadline fires.

Add cancellation-safety, shutdown-safety, and single-flight tests for rebuild. Use fake browser objects and a fake `browser_factory` that waits on an `asyncio.Event` so the test can trigger timeout, cancellation, or shutdown while rebuild is in progress. Force a rebuild by starting with a disconnected browser or by setting `consecutive_hangs` to the recycle threshold through behavior that already exists in tests. After the timed-out acquire, assert that the semaphore is not over-released, `BrowserPool.health()` is callable, there are no stale idle contexts from the old generation in pool state, and a later `run_with_page` using a factory that completes can successfully create a page. Add a concurrent acquire test where two or more callers all need a rebuild while the factory is gated; assert that the factory starts once, waiters do not deadlock, and all successful callers use the committed browser generation after the gate opens. Add a rebuild cleanup test where old idle context and old browser `close()` calls hang, the acquiring call times out during the gated factory, and the old resources are still removed from pool state. After one cleanup cap for that old-resource batch, assert the tracked cleanup task set is empty, the fake close calls were cancelled or completed by the bounded helper, and a later acquire works. Add a shutdown race test: start a rebuild with a gated factory, wait until the factory is in flight, call `BrowserPool.shutdown()`, release the factory with a fake browser, and assert `BrowserPool.health().browser_connected` is false, no browser remains in pool state, the locally created fake browser was scheduled into tracked cleanup rather than committed, and the active acquire reports shutdown through `PoolShutdown` or cancellation-specific handling rather than a generic `RuntimeError`. After one cleanup cap, assert that fake browser cleanup completed or was cancelled by the bounded helper and no cleanup task remains pending. These tests are accepted if they fail against unsafe, unsynchronized, or shutdown-racy rebuild behavior and pass after rebuild is cancellation-safe, shutdown-safe, and single-flight.

Add cleanup bounding, shutdown-drain, and lock-scope tests. Use fake page and context objects whose `close()` methods await an event that the test does not release. For the poisoned context case, make page close fail or force the context to be poisoned, then call `run_with_page(timeout_ms=100)` inside an outer test timeout comfortably larger than the cleanup cap. Assert the call returns or raises within the source deadline plus one cleanup cap, releases the semaphore, and does not return the poisoned context to the idle pool. Also assert that `BrowserPool.health()` or another acquire that only needs `self._lock` is not blocked for the full cleanup cap while a detached close is hanging. For old-generation cleanup, create two fake idle contexts plus a fake browser whose close calls all hang, trigger rebuild cleanup, and assert the cleanup task finishes after roughly one cleanup cap rather than three cleanup caps. For shutdown drain, trigger rebuild so a tracked cleanup task exists, call `BrowserPool.shutdown()` before that cleanup task naturally finishes, and assert shutdown bounded-awaits or cancels the task, clears `self._cleanup_tasks`, and produces no pending-task or unhandled-exception warnings. This proves cleanup is outside the source budget, bounded by batch, drained by shutdown, and not performed while holding the pool lock.

Add a post-call probe test. Monkeypatch or otherwise inject `job_harness.browser_pool.is_blocked` so it sleeps longer than the remaining deadline after `func(page)` has returned a sentinel value. Assert that `run_with_page` returns the sentinel value rather than raising `TimeoutError`. Add a companion assertion that a quick block probe still returns `BlockedResult(block=..., partial=value)` so anti-bot detection remains intact when it has time to run.

Update `plugins/job-harness/src/job_harness/search_engine.py` so `SearchEngine._run_browser_source` preserves partial listings from blocked browser calls. In the `isinstance(result, BlockedResult)` branch, keep `state = SourceState.BLOCKED`, keep the block-derived `failure_mode`, and keep the block error message. Also, if `result.partial` is a list of `RawListing`, assign it to `listings`. This makes `BlockedResult(block=..., partial=value)` meaningful at the search layer instead of silently dropping the partial value.

Update shutdown handling so pool lifecycle errors do not become parse errors. Add `PoolShutdown` in `plugins/job-harness/src/job_harness/browser_pool.py` or use an equivalent explicit shutdown signal. Raise it when acquisition observes `self._state.shutting_down` or when shutdown wins the rebuild factory race. In `plugins/job-harness/src/job_harness/search_engine.py`, catch `PoolShutdown` before the generic `Exception` branch and return a cancelled source outcome using `SourceState.CANCELLED`, `FailureMode.USER_CANCELLED`, and an error message such as `browser pool is shutting down`. If the whole search task is already being cancelled, preserve the current `asyncio.CancelledError` behavior and re-raise cancellation. Do not allow a generic `RuntimeError("pool is shutting down")` to reach `_run_browser_source` and become `FailureMode.PARSE_ERROR`.

Add a search-level regression test in `plugins/job-harness/tests/test_search_engine.py` or a nearby browser dispatch test. Do not reuse the existing `_BrowserScraper` class in that file because it only sets `requires_browser=True` and does not implement `search_with_page`, while the engine calls `scraper.search_with_page(page, params)`. Instead, create three small subclasses of `BaseBrowserScraper` inside the test, each with an async `search_with_page` method. Use `_RegistryContext` to register them under three fake source names. Use `FakeBrowser`, `FakeContext`, and `FakePage` from `plugins/job-harness/tests/_support/fake_browser.py`. Use `asyncio.Event` objects to make the first two sources occupy both contexts before the third source starts waiting; then release the first two and assert the third source obtains a context and returns a `RawListing`. Run a `SearchEngine` with a real `BrowserPool(max_contexts=2, acquire_timeout_ms=None, browser_factory=fake_factory)` and a `SourceRuntimeConfig` with a bounded but sufficient `source_attempt_timeout_ms`. Assert that all three browser sources finish without `FailureMode.POOL_ACQUIRE_TIMEOUT`. Add a separate search-level test where browser execution returns or produces `BlockedResult` with one partial `RawListing`. Because ordinary `engine.execute()` callers inspect persisted artifacts rather than raw `SourceOutcome` objects, assert observable outputs: the relevant `summary.source_statuses[...]` entry has `state == "blocked"`, that source or the raw-search summary reports `listings_written == 1`, `result.summary["raw_search"]["listings_written"]` increases by one for the partial listing, and `raw_search.jsonl` contains the partial listing record. These tests prove the fix at the same layer where BR-007 appears and prove partial data is not lost in user-visible artifacts.

Do not change `career:vk` or `career:ibs` parser logic for this bug unless deterministic tests expose a parser bug. The current evidence in this plan proves a shared browser pool scheduling failure, not a VK-specific or IBS-specific parsing failure. Do not add VK-specific delays, source-specific retries, or parser workarounds as part of BR-007.

After the runtime change, review the CLI and MCP pool construction. If `BrowserPool` defaults to deadline-aware acquire behavior, `plugins/job-harness/src/job_harness/cli.py` and `plugins/job-harness/scripts/mcp-server.py` can keep `BrowserPool(max_contexts=2)`. If an implementation chooses not to change the default, then both call sites must explicitly pass the new deadline-aware option. The preferred implementation is to change the default so any future pool call site gets the safer behavior.

Update neighboring documentation after the code and tests are stable. In `plans/search-layer-architecture.plan.md`, revise the browser runtime contract text that says `acquire_timeout_ms=5_000` must be left as a constructor default. In `plans/resilient-scraping.md`, revise the timeout table and BrowserPool sketch so they describe deadline-aware acquire behavior instead of a fixed default five-second acquire timeout. Record in those docs that BR-007 superseded the old fixed acquire timeout.

## Concrete Steps

Begin from a clean branch:

    cd /Users/user/Documents/repos/qa-job-harness
    git branch --show-current
    git status --short

Expected before implementation:

    codex/br-007-browser-pool-plan
    ?? plans/browser-pool-deadline-queue.plan.md

Reproduce BR-007 before changing code, if needed:

    cd /Users/user/Documents/repos/qa-job-harness
    uv --directory plugins/job-harness run job-harness search --query "manual qa" --sources hh_ru,hh_kz,hh_uz,rabota_by,headhunter_kg,career:ibs,career:vk --max-results 5 --format json

On the current code, the important failing evidence is in `summary.source_statuses`:

    "source": "career:vk",
    "state": "timeout",
    "failure_mode": "pool_acquire_timeout",
    "error": "could not acquire a context within 5000 ms"

Edit `plugins/job-harness/src/job_harness/browser_pool.py` first. Import `monotonic` from `time` or use an existing monotonic import if one is added locally. Add small private helpers only if they make the deadline arithmetic clearer, for example `_remaining_timeout_s(deadline: float) -> float`. Keep the change local to `BrowserPool` unless tests prove a broader change is needed.

Run the focused tests while iterating:

    cd /Users/user/Documents/repos/qa-job-harness/plugins/job-harness
    uv --directory . run python -m unittest discover -s tests -p 'test_browser_pool.py'
    uv --directory . run python -m unittest discover -s tests -p 'test_search_engine.py'

Then rerun the BR-007 CLI scenario from the repository root:

    cd /Users/user/Documents/repos/qa-job-harness
    uv --directory plugins/job-harness run job-harness search --query "manual qa" --sources hh_ru,hh_kz,hh_uz,rabota_by,headhunter_kg,career:ibs,career:vk --max-results 5 --format json

Expected after implementation: the JSON should not contain an early `pool_acquire_timeout` for `career:vk` or `career:ibs`. Under the current VPN environment, `hh_ru` may still report `blocked/anti_bot_page`; that is acceptable and unrelated to BR-007. `career:vk` and `career:ibs` should either return listings, report a real page-level block if their sites block access, or, only under severe contention, report `pool_acquire_timeout` with the final attempt's `elapsed_ms` near the source attempt timeout rather than near 5000 ms. The source status `attempts` and `retries` fields show whether earlier attempts also ran. A result such as `elapsed_ms` around 5000 and `error: could not acquire a context within 5000 ms` means the old fixed acquire timeout is still active and the implementation is not accepted.

Finally run the canonical gate from the repository root:

    cd /Users/user/Documents/repos/qa-job-harness
    python3 scripts/verify_repo.py full

Expected output includes:

    All checks passed!
    Success: no issues found in 64 source files
    Ran ... tests ... OK
    live registered source smoke passed: 19 sources

The exact number of tests may increase after adding regression tests.

## Validation and Acceptance

The implementation is accepted when all of the following are true.

First, unit tests prove the queue behavior. An explicit small acquire timeout still raises `PoolAcquireTimeout` quickly, preserving infrastructure guard behavior. An explicit large acquire timeout is capped by the smaller per-call deadline, so a call with `acquire_timeout_ms=5000` and `timeout_ms=100` fails near the 100 ms source deadline, not near five seconds. A pool with `acquire_timeout_ms=None` allows a queued caller to wait for a context and complete as long as the total call deadline has not expired.

Second, unit tests prove timeout, cancellation, shutdown, checkout, and cleanup safety around operations added to the deadline. A timeout or cancellation during browser rebuild must leave the pool usable for a later acquire, with no leaked semaphore slot and no stale idle contexts from the old browser generation. A failed or timed-out `browser.new_context()` before checkout must not decrement `in_use` for another active caller. If `browser.new_context()` returns after shutdown or after the browser generation changed, the returned context must be scheduled into tracked cleanup, `in_use` must not be incremented, and the failed acquisition must not call release for a context it never checked out. If the source deadline fires immediately after a local context or browser is created but before it is accepted into pool state, cleanup for that unaccepted resource must still complete through a tracked or shielded bounded cleanup task. Concurrent acquire calls that all need a rebuild must trigger only one browser factory call and must not deadlock on `self._lock`. If `shutdown()` runs while a rebuild factory is gated, the browser returned by that factory must be scheduled into tracked cleanup and must not remain committed in pool state. Old idle context/browser cleanup detached during rebuild must continue in a tracked cleanup task that is not cancelled by the source deadline; after one cleanup cap for that cleanup batch, no cleanup task should remain pending and the pool should hold no references to the old resources. Cleanup awaits such as poisoned `context.close()` must be bounded by the independent cleanup timeout; they may extend wall-clock runtime beyond the source deadline by that cleanup cap, but must not hang indefinitely or hold `self._lock` while closing. `shutdown()` must snapshot and drain or cancel tracked cleanup tasks so no pool-owned cleanup task remains pending after shutdown returns. A timeout in the post-call block probe after `func(page)` has returned must return the already successful value instead of raising `TimeoutError`; a fast block probe must still return `BlockedResult` with the successful value as `partial`. Pool shutdown during acquisition must not surface as `FailureMode.PARSE_ERROR`; it must either propagate `asyncio.CancelledError` for whole-run cancellation or map an explicit `PoolShutdown` signal to a cancelled source status.

Third, search-level regression tests prove the engine behavior. One test proves that three fake browser sources with `max_contexts=2` all complete when the third waits behind the first two. The test must use real `BaseBrowserScraper` subclasses with async `search_with_page` methods, `_RegistryContext`, `FakeBrowser`, `FakeContext`, `FakePage`, and `asyncio.Event` synchronization. It should inspect source statuses and fail if any source reports `FailureMode.POOL_ACQUIRE_TIMEOUT`. Another test proves blocked partial listings through artifacts available from normal execution: the relevant `summary.source_statuses[...]` entry has `state == "blocked"`, `listings_written == 1` is visible for that source or in the raw-search summary, `result.summary["raw_search"]["listings_written"]` includes the partial listing, and `raw_search.jsonl` contains the partial listing record.

Fourth, the real CLI scenario that reproduced BR-007 no longer reports an early `pool_acquire_timeout` for `career:vk` or `career:ibs`:

    uv --directory plugins/job-harness run job-harness search --query "manual qa" --sources hh_ru,hh_kz,hh_uz,rabota_by,headhunter_kg,career:ibs,career:vk --max-results 5 --format json

If a `pool_acquire_timeout` remains in this live scenario, inspect the source status. It is acceptable only when the final attempt's `elapsed_ms` is close to `SourceRuntimeConfig.source_attempt_timeout_ms`; `attempts` and `retries` show whether earlier attempts also ran. It is not acceptable when it happens around 5000 ms with the old fixed-timeout message.

Fifth, documentation that still describes `BrowserPool.acquire_timeout_ms=5_000` as the desired default is updated. Specifically, `plans/search-layer-architecture.plan.md` and `plans/resilient-scraping.md` should no longer contradict this plan.

Sixth, the full repository gate passes:

    python3 scripts/verify_repo.py full

This is an internal runtime change, so the observable behavior is in source statuses. The user should see fewer false `timeout/pool_acquire_timeout` errors during broad searches. Site-specific blocks remain visible as `blocked` states, and real source deadline exhaustion remains visible as `timeout`.

## Idempotence and Recovery

The implementation is safe to repeat. Running the focused tests and the full gate does not modify source files. The CLI reproduction writes temporary run directories under the system temporary directory and cleans them up automatically.

If a test run hangs, stop only that test process and inspect whether a fake task failed to release its gate or whether `BrowserPool.shutdown()` was not awaited. Do not use destructive git commands. Use `git diff` to inspect local edits and `git status --short` to confirm the working tree.

If the deadline-aware implementation accidentally releases a semaphore after a failed acquire, tests may show too many concurrent workers or context reuse errors. Recover by adding an explicit `acquired = False` flag, setting it immediately after a successful `self._sem.acquire()`, and releasing only when `acquired` is true.

If a cancellation during browser rebuild leaves the pool unable to create future pages, inspect `_rebuild_browser_locked()` for awaited calls that still occur while `self._state` points at resources being torn down. Recover by detaching old resources from shared state before awaiting close or factory work, and by committing a new browser to shared state only after the factory returns successfully.

If concurrent rebuild tests show multiple browser factory calls, inspect whether rebuild serialization is separate from `self._lock`. Recover by adding or using `self._rebuild_lock` around the slow rebuild path, and by rechecking whether rebuild is still needed after acquiring that rebuild lock. Do not solve this by making a helper acquire `self._lock` while the caller already holds it.

If a shutdown-race test leaves a new browser committed after shutdown, inspect the rebuild commit section. Recover by checking `self._state.shutting_down` under `self._lock` immediately before assigning `self._state.browser = new_browser`; when shutdown has started, schedule `new_browser` into the tracked bounded cleanup path and do not commit it. Do not await `new_browser.close()` inline in the acquisition path.

If rebuild cleanup tests leave pending cleanup tasks after the cleanup cap, inspect the tracked cleanup task implementation. Recover by adding a task set on `BrowserPool`, removing tasks in a done callback or `finally` block, and routing old-resource close calls through the bounded cleanup helper. Do not await old context or old browser close inside `_acquire_context_locked()` under the source-result deadline.

If failed `browser.new_context()` tests corrupt `contexts_in_use`, inspect the checkout bookkeeping. Recover by adding a `checked_out = False` flag in `run_with_page`, setting it only after acquisition returns an accepted context, and calling context release only when `checked_out` is true. In the acquisition helper, increment `self._state.in_use` only under `self._lock` after `new_context()` has returned and the pool accepts that context as current.

If rejected `new_context()` or shutdown-loser `new_browser` cleanup tests leak resources, inspect whether their close calls are awaited inline inside acquisition. Recover by sending those local resources through the tracked cleanup task path, or by creating and tracking a shielded bounded cleanup task before returning, retrying, or raising from acquisition.

If cleanup tests hang or `health()` blocks while cleanup is hanging, inspect close calls in `finally`, `_release_context_locked`, `shutdown`, and rebuild. Recover by detaching or updating state under `self._lock`, releasing the lock, and then routing all `page.close()`, `context.close()`, and `browser.close()` calls through a bounded helper outside the lock. Do not use the remaining source deadline for cleanup after a value has already been produced; use the independent cleanup cap.

If shutdown leaves pending cleanup tasks, inspect `BrowserPool.shutdown()`. Recover by snapshotting `self._cleanup_tasks` under `self._lock`, awaiting or cancelling them with the cleanup batch cap outside the lock, observing task exceptions, and clearing the task set before returning.

If shutdown during acquisition appears as `parse_error`, inspect exception types and `SearchEngine._run_browser_source` handler order. Recover by replacing generic shutdown `RuntimeError` with `PoolShutdown` or cancellation-specific handling, and catch that signal before the generic `Exception` branch.

If broad live searches still report `pool_acquire_timeout`, inspect the final attempt's elapsed time and the source `attempts` and `retries` fields. If the final attempt waited for nearly the full source attempt budget before timing out, the pool is truly saturated and the status is legitimate. If it still fails near 5000 ms, the fixed acquire timeout remains in the production path and the CLI or MCP call site is still using the old behavior.

If a broad search reports `goto_timeout` after this change, inspect whether the timeout occurred while creating or rebuilding a browser context, opening a page, or running the scraper. This classification is expected for timeouts after the semaphore was acquired. It is not expected for a timeout in the post-call block probe after listings were already returned by the scraper; that case should return the successful value. If setup or scraper `goto_timeout` happens frequently, the next fix should tune source attempt budgets or browser context reuse, not reintroduce a short acquire timeout.

## Artifacts and Notes

Current reproduction summary from before implementation:

    source=career:vk
    state=timeout
    failure_mode=pool_acquire_timeout
    attempts=2
    retries=1
    error=could not acquire a context within 5000 ms

Current relevant defaults and superseded documentation after implementation:

    SourceRuntimeConfig.source_attempt_timeout_ms = 30000
    BrowserPool.max_contexts = 2
    BrowserPool.acquire_timeout_ms = None
    CLI pool construction = BrowserPool(max_contexts=2)
    MCP pool construction = BrowserPool(max_contexts=2)
    plans/search-layer-architecture.plan.md records that BR-007 superseded acquire_timeout_ms=5000
    plans/resilient-scraping.md documents deadline-aware acquire behavior

This plan intentionally does not propose full browser parallelization. The runtime should remain bounded because many simultaneous browser pages from one VPN/IP can increase anti-bot blocks and browser resource contention. The fix is fair queueing within a deadline, not unbounded concurrency.

Important behavioral edge cases covered by this plan:

    explicit acquire_timeout_ms is capped by timeout_ms
    timeout during browser rebuild must leave the pool coherent
    failed browser.new_context before checkout does not decrement another caller's in_use count
    rejected new_context contexts and shutdown-loser new_browser resources use tracked cleanup outside source deadline
    concurrent rebuild demand starts exactly one browser factory
    shutdown during gated rebuild does not leave a committed browser
    old-generation rebuild cleanup is detached, tracked, and not cancelled by source deadline
    cleanup is outside source budget, cleanup batch duration is capped once per batch, and cleanup close does not hold self._lock
    shutdown drains tracked cleanup tasks before returning
    pool shutdown during acquisition is not reported as parse_error
    page.set_default_timeout must never receive 0 from remaining deadline math
    timeout during post-call is_blocked(page) after func(page) succeeded returns the value
    BlockedResult.partial listings are preserved in search artifacts
    browser source elapsed_ms is per attempt; attempts and retries describe repeated attempts

## Interfaces and Dependencies

The main interface to preserve is:

    BrowserPool.run_with_page(func, *, timeout_ms: int | None = None) -> T | BlockedResult[T]

At the end of the implementation, this method should treat `timeout_ms` as the total per-call budget for waiting for a context, creating a new browser/context, opening a page, running the page function, and running the post-call block probe. Existing callers in `SearchEngine._run_browser_source` should not need a signature change. The method should not raise `TimeoutError` from the post-call block probe after `func(page)` has already returned a value; in that case it should return the value, or return `BlockedResult(block=..., partial=value)` if the probe completes quickly with a block signal. Cleanup after result or exception is not part of this source-result budget, but must use a bounded cleanup timeout for every close await. Old browser-generation cleanup from rebuild and cleanup for locally created but unaccepted resources are specifically outside this budget and should run in tracked cleanup tasks or shielded bounded tasks owned by the pool. `run_with_page` should track whether a context was actually checked out. It should release a context and decrement `in_use` only when checkout succeeded; failed acquisition before checkout should release only the semaphore.

The constructor in `plugins/job-harness/src/job_harness/browser_pool.py` should become:

    def __init__(
        self,
        *,
        max_contexts: int = 2,
        page_timeout_ms: int = 30_000,
        acquire_timeout_ms: int | None = None,
        recycle_after_consecutive_hangs: int = 2,
        browser_factory: Callable[[], Awaitable[Any]] | None = None,
        context_kwargs: dict[str, Any] | None = None,
    ) -> None:

If `acquire_timeout_ms` is not `None`, it must be at least 1. If it is `None`, no fixed acquire cap is used; the call deadline is the cap. The validation in `__init__` should reflect this.

`PoolAcquireTimeout` remains the exception type for failing to acquire the semaphore before a context is assigned. `SearchEngine._run_browser_source` can continue mapping it to `SourceState.TIMEOUT` and `FailureMode.POOL_ACQUIRE_TIMEOUT`. The change should make that path rare in normal operation, not remove it. Timeouts after the semaphore is acquired should be plain `TimeoutError` so `SearchEngine._run_browser_source` maps them to `FailureMode.GOTO_TIMEOUT`.

`BrowserPool._rebuild_browser_locked()` may be refactored even though it is an internal helper. Its required interface is behavioral: if cancellation happens while the new browser factory is awaiting, the shared pool state remains valid and a later `run_with_page` can retry. It should avoid holding references to torn-down idle contexts in `self._state.contexts` and should not commit a partially created browser to `self._state.browser`. Rebuild must be single-flight through a separate rebuild lock or equivalent shared task. Do not design this as a helper that acquires `self._lock` while the caller already holds `self._lock`. Before committing a new browser, rebuild must check `self._state.shutting_down`; if shutdown has started, it must schedule the locally created browser into the tracked bounded cleanup path and leave pool state without a browser. Old contexts, old browser resources detached for rebuild, and locally created shutdown-loser browsers should be passed to tracked cleanup tasks and not awaited inside the source-deadline acquisition call.

Context creation has a separate checkout contract. `browser.new_context()` should be awaited outside `self._lock` using a local browser reference captured under the lock. After it returns, reacquire `self._lock` and accept the context only if the pool is not shutting down and the local browser is still `self._state.browser`. Increment `self._state.in_use` only when accepting the context. If the pool rejects the returned context because shutdown started or the browser generation changed, schedule the returned context into tracked bounded cleanup and do not increment or decrement `in_use`. That cleanup must not be awaited inline inside the source-deadline acquisition function.

Tracked cleanup task ownership belongs to `BrowserPool`. The pool should maintain a set of cleanup tasks for detached old resources and for local resources created but not accepted into pool state. Cleanup batches close multiple resources concurrently under one cleanup timeout cap for the batch. `shutdown()` should snapshot those tasks under `self._lock`, await or cancel them outside the lock with the cleanup batch cap, observe exceptions, clear the set, and return with no pool-owned cleanup task pending.

Add a specific shutdown signal such as:

    class PoolShutdown(RuntimeError):
        """Raised when browser pool acquisition loses a race with shutdown."""

`SearchEngine._run_browser_source` should catch `PoolShutdown` before generic `Exception` and map it to `SourceState.CANCELLED` with `FailureMode.USER_CANCELLED`, unless the surrounding task is already cancelled and `asyncio.CancelledError` should propagate.

`SearchEngine._run_browser_source` should preserve partial listings from blocked browser execution. When `result` is a `BlockedResult` and `result.partial` is a list, assign that list to the attempt's `listings` while keeping the blocked source state and block failure mode. This changes only the data retained from an already blocked attempt; it does not change block classification. Tests should assert this through normal search outputs: blocked source status, `listings_written`, raw-search summary counts, and the `raw_search.jsonl` record.

No new third-party dependency is needed. Use Python standard library time measurement through `time.monotonic`.

Revision note, 2026-06-12 / Codex: Initial ExecPlan written after reproducing BR-007 on `main`. The plan chooses deadline-aware bounded queueing because it fixes the shared browser scheduling failure for all browser scrapers without adding site-specific behavior or unbounded parallelism.

Revision note, 2026-06-14 / Codex: Updated after review to make the deadline cover `_acquire_context_locked()` and `context.new_page()`, distinguish `PoolAcquireTimeout` from post-acquire `TimeoutError`, relax live-scenario acceptance to allow only near-deadline pool acquire timeouts, specify deterministic fake-browser regression tests, mark older `acquire_timeout_ms=5_000` plan text as superseded, and add implementation milestones.

Revision note, 2026-06-14 / Codex: Updated after review to require cancellation-safe browser rebuilds, preserve successful scraper values when the post-call block probe times out, add a test for explicit acquire timeout being capped by the smaller per-call deadline, clarify that browser source `elapsed_ms` is per attempt rather than including retry backoff, and require Playwright timeout values to be clamped to at least 1 ms.

Revision note, 2026-06-14 / Codex: Updated after review to specify single-flight browser rebuild serialization without reentrant `self._lock` deadlocks, require `SearchEngine` to retain `BlockedResult.partial` listings, and replace an unsupported parser-health claim with a neutral parser-scope boundary.

Revision note, 2026-06-14 / Codex: Updated after review to make rebuild shutdown-safe, state that cleanup is outside the source-result deadline but independently bounded, add tests for shutdown during gated rebuild and hanging cleanup close calls, and make blocked partial acceptance observable through search summaries and `raw_search.jsonl`.

Revision note, 2026-06-14 / Codex: Updated after review to resolve the rebuild cleanup budget conflict by detaching old browser-generation resources under lock and closing them in tracked bounded cleanup tasks outside the source deadline, require cleanup close calls to run outside `self._lock`, and require explicit pool-shutdown handling so shutdown races cannot become `parse_error`.

Revision note, 2026-06-14 / Codex: Updated after review to define `browser.new_context()` as an outside-lock operation with an explicit post-return checkout step, protect `in_use` from failed acquisitions before checkout, require `shutdown()` to drain tracked cleanup tasks before returning, and define cleanup timeout as one wall-clock cap per cleanup batch.

Revision note, 2026-06-14 / Codex: Updated after review to require rejected `new_context()` contexts and shutdown-loser `new_browser` instances to be cleaned through tracked or shielded bounded cleanup outside the source-deadline acquisition function.
