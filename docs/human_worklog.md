# Human Worklog

AI (e.g., Claude) should **NOT** read this document.

## What is this document?
- This is a working log for human. 

## Future Enhancements

1. Memory

    - Today, there is no memory on the Chat panel. How do we add memory?

2. QA Agent
    
    - After the answering agent answers it, we want to pass the infromation to a QA agent to further validate the result.
    - The QA Agent can have its own tool.
    - We want the UI to see that `QAing now...`


3. Use AI to see if we miss any technical highlights.
    - Ask AI to list out potential highlights that are missing
    - User confirm which one to add
    - Then AI add

4. Use AI to upgrade python package

    - Ask AI to check for the packages we use
    - Upgrade them
    - Run tests
    - Fix test errors
    - Summarize the change

## Work Log

### Aug 19, 2026

#### To Dos

- [Done] Annotation change

    - In `src/health_coverage_navigator/web/client.py`, `tavily_client(..)` returns Any, and `WebSearchClient._client` is of of type Any. Change them to `AsyncTavilyClient`.

    - This means `AsyncTavilyClient` should be imported at the top of the module

- [Done] Manual validation on the FE

    - What are the "citations" for web? Is it the full text? Is it the URL? Verify this from the UI

- [Done] Make sure the metrics in README.md is updated

- Use walkthrough to trace the code

    - In process, add to technical highlights if any is identified.


### Aug 18, 2026

#### To Dos

- [Done] Open the frontend, and validate it worked

    - Specifcally check the use of SQL tool

- [Done] Try running evals with concurrency = 5

    - If still getting 200k 429 rate limit error, try create a new API key.

- [Done] Use walkthrough to understand what has been implenented.

- [Done] Document `how do we design the guard to have AI write SQL`, and add to technical_highlights.md


### Aug 16, 2026

#### To Dos

- Update the plan.md to include a step 1c to include a SQL for `exchange_puf` and `part_d_spuf`
   - Check and if this mens we shall remove phase 5

#### Questions

- Where does it store the embeded vector?


#### Logs

1. Phase 1-b plan document
    - /Users/andytseng/.claude/plans/i-am-read-to-eager-wind.md

### Aug 14, 2026

#### To Dos
- [FUTURE] Ask AI to move `src/health_coverage_navigator/agent/prompt.py` into a markdown file and stored in in a `prompts` directory. It should be versioned, and which prompt to load should be defined by the `config.yaml`

- [Done] Write a integration test for agent and use Pytest to annotate `integration_test`

- [Done] Continue to understand the code

- [Done] Use the app end to end

- [Done] Create a skill to walk through the changes. Step by Step.
   
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
