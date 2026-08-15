# Human Worklog

## What is this document?
- This is a working log for human. 
- AI (e.g., Claude) should **NOT** read this document.

## Work Log

### Aug 14, 2026

#### To Dos
- [FUTURE] Ask AI to move `src/health_coverage_navigator/agent/prompt.py` into a markdown file and stored in in a `prompts` directory. It should be versioned, and which prompt to load should be defined by the `config.yaml`

- [Done] Write a integration test for agent and use Pytest to annotate `integration_test`

- Continue to understand the code

    ```bash
    12 steps total. You've finished 6 — step 7 is next.

    #	Step	
    1	BM25 ranking engine	✅
    2	Corpus index	✅
    3	Agent types, prompt, tools	✅
    4	Agent runtime	✅
    5	API wiring	✅
    6	Evals	✅
    7	Config additions (config.py, config.yaml)	← next
    8	Tests (conftest.py + 6 new files)	
    9	Frontend (schema.d.ts, 3 components)	
    10	Makefile	
    11	Docs	
    12	Two bug fixes worth seeing in isolation	
    Worth noting: steps 7 and 10 have both grown since I listed them. Config now also carries agent.request_retries (the 429 fix), and the Makefile picked up smoke / smoke-abstain plus the types-check fold-in. I'll cover the additions when we get there rather than treating them as separate.

    Side work we did between steps, all outside the original 12: make smoke, eval progress logging, --concurrency, the async conversion, and the CLAUDE.md async-first rule.

    Say "go to next step" when you want step 7.

    ```

- Use the app end to end

- Create a skill to walk through the changes. Step by Step.
   
   - Sample prompts
    ```
    1. what changes you made
    2. What's the purpose of the change
    3. The files I should be looking
    4. PAUSE. I will then read the files myself, and may ask you follow up questions.
    ```





### Aug 13, 2026

#### Questions
1. Understand how the frontend work.
    - Create a architecture diagram with mergaid diagram
    - [Done] Understand `_mount_frontend` in `src/health_coverage_navigator/api/app.py`
    - [Done] What are the contract between frontend and backend? `make types` ?
    - Add a skill for "sync backend to frontend"
        - Try it out
    - [Done] How to start the frontend?
