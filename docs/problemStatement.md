# AI-Powered Restaurant Recommendation Service (Zomato-Inspired)

## Overview

Build an AI-powered restaurant recommendation service inspired by Zomato. The system intelligently suggests restaurants based on user preferences by combining structured data (a real restaurant dataset) with a Large Language Model (LLM) for reasoning, ranking, and natural-language explanations.

## Objective

Design and implement an application that:

- Takes user preferences (such as location, budget, cuisine, and ratings)
- Uses a real-world dataset of restaurants
- Leverages an LLM to generate personalized, human-like recommendations
- Displays clear and useful results to the user

## System Workflow

### 1. Data Ingestion

- Load and preprocess the Zomato dataset from Hugging Face: [ManikaSaini/zomato-restaurant-recommendation](https://huggingface.co/datasets/ManikaSaini/zomato-restaurant-recommendation)
- Extract relevant fields such as restaurant name, location, cuisine, cost, rating, etc.

### 2. User Input

Collect user preferences:

| Preference | Description / Examples |
| --- | --- |
| Location | Delhi, Bangalore |
| Budget | low, medium, high |
| Cuisine | Italian, Chinese |
| Minimum rating | e.g. 4.0 and above |
| Additional preferences | family-friendly, quick service |

### 3. Integration Layer

- Filter and prepare relevant restaurant data based on user input
- Pass the structured results into an LLM prompt
- Design a prompt that helps the LLM reason about and rank the options

### 4. Recommendation Engine

Use the LLM to:

- Rank restaurants
- Provide explanations for why each recommendation fits the user's preferences
- Optionally summarize the set of choices

### 5. Output Display

Present the top recommendations in a user-friendly format, showing for each:

- Restaurant name
- Cuisine
- Rating
- Estimated cost
- AI-generated explanation

## Data Flow Summary

```
Hugging Face dataset
        │
        ▼
Data ingestion & preprocessing  ──►  structured restaurant records
        │
        ▼
User preferences (location, budget, cuisine, min rating, extras)
        │
        ▼
Integration layer: filter candidates ──► build LLM prompt
        │
        ▼
LLM recommendation engine: rank + explain (+ optional summary)
        │
        ▼
Output display: name, cuisine, rating, estimated cost, explanation
```

## Scope Notes

- The dataset is the source of truth for restaurant facts; the LLM handles ranking, reasoning, and explanation rather than inventing restaurant data.
- Filtering happens before the LLM call, so only relevant candidates are passed into the prompt.
- Summarizing the overall set of choices is optional, not required.
