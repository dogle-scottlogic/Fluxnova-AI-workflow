# Fluxnova-AI-workflow

A Workflow for FLuxnova which incorporates AI and an Eval framework

## Overview

This project will keep track of the bpmn under test and the overall Fluxnova specific implementation of the eval
framework.

## Tech

Proposal to use DeepEval upfront as the eval framework


## Flow

First draft proposal for the flow would be:

- Update bpmn
- Deploy to a sandbox environment
- Initiate the bpmn
- Handle any pre-tasks / api calls required to complete the target AI task(s)
- Collate the AI Events into a single response Object
- Eval with DeepEval

## Events

This approach will require some changes on Fluxnova.

Each AI call in the subprocess should emit a custom event. e.g.

```json
{
  "type": "agentic_subprocess_iteration",
  "iteration": 1,
  "input": {
    "prompt": "",
    "tool-calls": []
  },
  "output": {
    "message": "",
    "tool-calls": []
  }
}
```

The pipeline should collate the events into a single eval object such as:

```json
{
  "goal":"Generate risk report",
  "finalOutput":"",
  "iterations":4,
  "toolCalls":[],
  "executionTime":4200,
  "step-history": []
}
```