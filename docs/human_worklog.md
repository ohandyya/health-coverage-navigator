# Human Worklog

## What is this document?
- This is a working log for human. 
- AI (e.g., Claude) should **NOT** read this document.

## Work Log

### Aug 14, 2026

#### To Dos
- [FUTURE] Ask AI to move `src/health_coverage_navigator/agent/prompt.py` into a markdown file and stored in in a `prompts` directory. It should be versioned, and which prompt to load should be defined by the `config.yaml`

- [Done] Write a integration test for agent and use Pytest to annotate `integration_test`

- [Done] Continue to understand the code

- [Done] Use the app end to end

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
