---
name: code-review-top-selector
description: Filter Top 75 high-quality code review comments from CSV files. Uses AI to analyze each review comment's type and quality grade (a/b/c/d), prioritizes d-grade and c-grade comments. Use when processing code review CSV files, filtering high-quality review comments, or generating review reports.
---

# Code Review Top Selector

## Overview

This skill filters the Top 75 high-quality code review comments from a CSV file and outputs a new CSV file.

### Quality Grade Standards

- **Grade a**: Contains only inquiries or pure restatement of coding standards
  - Example: "Single line code should not exceed 80 characters"
- **Grade b**: Clearly identifies the problem and states what to do
  - Example: "Suggest using relative paths instead of absolute paths"
- **Grade c**: Demonstrates thinking and explains how to design the code
  - Example: "This flag is not meaningful and increases cyclomatic complexity. If the condition is not met, just log it and return failure."
- **Grade d**: Builds on grade c by explaining why or how to improve
  - Example: Detailed problem analysis + specific improvement plan + code examples

### Filtering Strategy

1. **Validity Check**: 
   - Only process comments that start with `【review】`
   - Ignore all other comments (considered invalid)

2. **Deduplication**: 
   - Remove duplicate review comments based on content
   - Keep only unique review comments
   - When duplicates exist, keep the first occurrence

3. **Priority Filtering**: 
   - Prioritize d-grade comments
   - If d-grade < 75, supplement with c-grade
   - Sort by quality in descending order

4. **Type Identification**:
   - Extract tags from content (e.g., 【review】【安全】)
   - If no explicit tags, AI determines type based on content

## Usage

### Method: AI-Assisted Processing

Tell the AI: "Please use the code-review-top-selector skill to process this CSV file"

The AI will process **each review comment one by one**:

1. Read the CSV file
2. **For each review comment:**
   - **Check validity**: Only process if it starts with `【review】`, skip others
   - **Deduplicate**: Track seen comments, skip duplicates
   - Analyze the review content
   - **Determine quality grade** (a/b/c/d) based on the standards
   - **Identify review type** (security/performance/standard/design/review/etc.)
3. After analyzing all unique valid comments, sort by quality grade
4. Filter Top 75 high-quality comments
5. Output a new CSV file with columns: Review Content, Review Time, Reviewer, Quality Grade, Review Type

## Output Format

Output CSV contains the following columns:
- **检视详情**: Original review comment
- **创建时间**: Retrieved from original CSV
- **检视者**: Retrieved from original CSV
- **质量等级**: a/b/c/d
- **检视类型**: 安全/性能/设计，等。

## Important Notes

1. **Validity Filter**: Only comments starting with `【review】` are processed
2. **Deduplication**: Duplicate comments are automatically removed
3. **Processing Approach**: AI analyzes each comment individually for accurate assessment
4. **Encoding Format**: Defaults to UTF-8 encoding
5. **Column Mapping**: If original CSV has different column names, AI will handle mapping automatically
6. **Quality Assessment**: AI assesses grades based on criteria, may have subjectivity
