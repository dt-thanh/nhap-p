# P100 - AbsorptionForecast AI Agent

## Project Overview

An AI agent that forecasts apartment absorption rates for a real estate sales team.
The pipeline combines Prophet for time-series forecasting with a LangGraph agent that orchestrates the workflow, a FastAPI backend, and a Next.js frontend.
Every forecast this agent produces must pass through a human-in-the-loop approval step before it is treated as final. This is a hard project requirement, not optional behavior.
[TODO: 1-2 more sentences if useful - e.g. monorepo vs. separate frontend/backend repos, current sprint focus]

## Setup & Build

Backend: [TODO: exact command, e.g. `uv sync` or `pip install -r requirements.txt`, then `uvicorn app.main:app --reload`]
Frontend: [TODO: exact command, e.g. `npm install && npm run dev`]
Environment variables: [TODO: list required .env keys, or point to .env.example]

## Test Commands

Backend: [TODO: e.g. `pytest tests/ -v`, and how to run a single test]
Frontend: [TODO: e.g. `npm run test`, `npm run lint`]

## Code Style

[TODO: formatter/linter in use - e.g. black + ruff for Python, eslint + prettier for Next.js. Cite the config file if one exists, e.g. pyproject.toml, .eslintrc]

## Project Structure

[TODO: top 2-3 levels of the folder tree with a one-line description per top-level folder]

## Boundaries & Constraints

* Never bypass or auto-approve the human-in-the-loop review step in the forecasting pipeline.
* Never commit secrets, API keys, or `.env` files.
* Never manually edit CHANGELOG.md or any auto-generated file (Prophet model artifacts, generated migrations, etc.).
* [TODO: any other files/folders that are off-limits or managed by another tool]

## Team Workflow

* This repo is part of a 4-person capstone team (Team Alpha) with a sprint backlog spanning 4 sprints.
  Follow the active sprint's user stories; flag before working outside the current sprint's scope.
* [TODO: branch naming convention, commit message format, PR requirements if the team has agreed on one]
