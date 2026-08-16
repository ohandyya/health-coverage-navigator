# Human Worklog

AI (e.g., Claude) should **NOT** read this document.

## What is this document?
- This is a working log for human. 

## Future Enhancements

1. Memory

    - Today, there is no memory on the Chat panel. How do we add memory?

## Work Log

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
