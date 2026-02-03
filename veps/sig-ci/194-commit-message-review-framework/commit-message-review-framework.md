# VEP #194: Review Framework for Commit Message Quality Assurance

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP proposes integrating an AI-based review framework into the KubeVirt enhancements repository to improve quality assurance, consistency, and reduce manual review burden. The framework will be implemented in three progressive stages: starting with simple commit message linters that provide warnings, progressing to AI-based analysis with persistent context, and ultimately making checks required to meet repository standards.

The framework focuses on ensuring consistent commit message formats, validating VEP content against template requirements, and providing actionable feedback to contributors. This enhancement will help maintain high quality standards as the repository grows and reduce the time reviewers spend on common issues.

## Motivation

As the KubeVirt enhancements repository grows, maintaining consistency across VEPs and commit messages becomes increasingly challenging. Manual review processes, while thorough, can be time-consuming and may miss subtle inconsistencies. Additionally, contributors may not always be aware of repository-specific conventions and standards.

Current challenges include:
- Inconsistent commit message formats across contributions
- Manual verification of VEP template completeness
- Lack of automated quality checks that can scale with repository growth
- Time spent by reviewers catching common issues that could be automated

An AI-based review framework addresses these challenges by providing automated checks that can catch issues early, provide consistent guidance to contributors, and reduce the manual review burden on SIG reviewers and maintainers.

## Goals

- **Stage 1**: Implement commit message linters with PR checklist warnings to establish consistent commit message formats
- **Stage 2**: Transition to AI-based analysis with persistent context (similar to CLAUDE.md pattern)
- **Stage 3**: Make checks required for repository standards, integrating with branch protection rules
- Establish consistent commit message format across the repository through automated validation
- Provide actionable, clear feedback to contributors on how to fix issues
- Reduce manual review time by catching common issues early

## Non Goals

- Replace human reviewers - automated checks complement human judgment by catching format and template compliance issues.
- Real-time blocking of PRs in initial stages - warnings first, requirements later

## Definition of Users

- **authors and contributors**: People creating or updating PR's who need guidance on formatting and standards
- **SIG reviewers and approvers**: Reviewers who benefit from automated checks catching issues before manual review
- **Release team members**: Team members responsible for ensuring code quality and consistency
- **Repository maintainers**: Maintainers who need scalable quality assurance mechanisms

## User Stories

- As a contributor, I want consistent commit message format guidance so I can write commits that meet repository standards without manual research
- As a contributor, I want automated feedback on my commit message so I can fix issues before requesting review
- As a reviewer, I want automated checks to catch common issues early so I can focus on the content of the commit
- As a maintainer, I want quality standards enforced automatically so the repository maintains consistency as it grows

## Repos

- kubevirt/kubevirt (As a starting place)

## Design

The AI-based review framework will be implemented in three progressive stages, allowing the community to adapt gradually and provide feedback at each stage.

### Stage 1: Commit Message Linters

The first stage focuses on establishing consistent commit message formats through automated linting.

#### Implementation Approach

- Integrate a commit message linting tool (e.g., commitlint, gitlint) via GitHub Actions
- Define commit message format rules through configuration files
- Run checks on all commits in pull requests
- Display warnings in PR checklists rather than blocking merges
- Provide clear error messages with examples of correct formats

#### Commit Message Format Exploration

Rather than mandating a specific format immediately, this stage will explore options:
- **Conventional Commits**: Widely adopted format with type prefixes (feat, fix, docs, etc.)
- **KubeVirt-specific format**: Custom format tailored to KubeVirt project needs
- **Hybrid approach**: Combining elements from multiple formats

The community will decide on the preferred format based on:
- Consistency with existing commit history
- Ease of adoption by contributors
- Integration with release tooling
- Alignment with KubeVirt project conventions

#### Configuration and Documentation

- Commit message format rules documented in repository guidelines
- Configuration files (e.g., `.commitlintrc.json`) committed to repository
- Examples of valid and invalid commit messages provided
- Integration with GitHub Actions for automated checking

### Stage 2: AI-Based Analysis

The second stage introduces AI-powered analysis for comprehensive VEP content validation.

#### Persistent Context File

A persistent context file (similar to the CLAUDE.md pattern used in other projects) will be created to provide AI systems with:
- Repository standards and conventions
- template requirements and expectations
- Project-specific instructions and guidelines
- Examples of high-quality commits

This file serves as a single source of truth for AI analysis, ensuring consistent evaluation criteria.

#### AI Analysis Integration

- Integrate AI analysis tool via GitHub Actions (e.g., using AI APIs like OpenAI, Anthropic, or GitHub Copilot)
- Analyze commit message against template requirements
- Validate formatting and structure
- Provide suggestions for improvement
- Flag potential issues before human review

#### Analysis Capabilities

The AI analysis will check for:
- **Template compliance**: All required sections present and properly formatted
- **Content quality**: Sections contain substantive content, not just placeholders
- **Consistency**: Naming conventions, formatting, and style consistency
- **Completeness**: Required links, examples, and documentation present
- **Clarity**: Writing quality and clarity of explanations

#### Feedback Mechanism

- AI analysis results displayed in PR comments or checklists
- Actionable suggestions provided for each identified issue
- Links to relevant documentation and examples
- Non-blocking warnings to allow iterative improvement

### Stage 3: Required Checks

The final stage makes quality checks mandatory for repository standards.

#### Status Check Integration

- Convert warnings to required GitHub status checks
- Integrate with branch protection rules
- Block PRs that don't meet quality standards
- Provide clear documentation on requirements and how to resolve issues

#### Branch Protection

- Configure branch protection rules to require passing checks
- Allow maintainers to override when necessary
- Ensure all PRs meet standards before merge

#### Documentation and Support

- Complete documentation on all requirements
- Troubleshooting guides for common issues
- Examples and templates for contributors
- Community adoption confirmed through usage metrics

### Implementation Considerations

#### Privacy and Security

If using external AI services:
- Evaluate data privacy implications
- Consider on-premises or self-hosted alternatives
- Ensure compliance with project security policies
- Review terms of service for AI providers

#### Gradual Rollout

- Start with warnings to allow community adaptation
- Gather feedback at each stage before progressing
- Adjust rules based on community input
- Provide grace periods for adoption

#### Tool Selection

Specific tools will be selected during implementation based on:
- Community preferences and expertise
- Integration capabilities with GitHub
- Maintenance requirements
- Cost considerations
- Open source alternatives availability

## API Examples

### Example Commit Message Formats

#### Conventional Commits Format
```
feat(vep): add AI review framework proposal

Implement automated commit message linting and AI-based VEP
content validation to improve repository quality standards.

Closes #194
```

#### KubeVirt-Specific Format (Example)
```
VEP: Add AI review framework (#194)

Propose integration of AI-based review framework for quality
assurance in enhancements repository.
```

### Example AI Context File Structure

```markdown
# KubeVirt Enhancements Repository Standards

## Commit Message Requirements

All commit message must include:
- Signed-off-by section
- Subject line (first line) with clear, concise description
- Body section with detailed explanation when needed
- Reference to related issues or PRs when applicable

## Commit Message Standards

Commit messages should:
- Use present tense ("add" not "added")
- Provide clear, concise description

## Documentation Standards

- Use clear, professional language
- Include code examples where applicable
- Link to relevant documentation
- Follow markdown formatting conventions
```

## Alternatives

### Alternative 1: Linters Only (Without AI)

**Approach**: Implement only commit message linters without AI-based analysis.

**Rejected because**: 
- Limited analysis capability - linters can only check format, not content quality
- Misses opportunities for comprehensive quality assurance
- Doesn't scale to validate complex documentation requirements

### Alternative 2: Manual Review Only

**Approach**: Continue with current manual review process without automation.

**Rejected because**:
- Doesn't scale with repository growth
- Inconsistent application of standards
- Higher time burden on reviewers
- Contributors may not be aware of all requirements

**Decision**: Evaluate during implementation based on project needs and constraints.

## Scalability

The AI-based review framework is designed to scale with repository growth:

- **Automated checks**: Scale automatically with number of PRs and commits
- **Consistent standards**: Ensures quality standards maintained regardless of repository size
- **Reduced manual overhead**: Frees reviewers to focus on substantive review rather than format checking
- **Parallel processing**: Can analyze multiple PRs simultaneously without resource constraints

As the repository grows, the framework will continue to provide consistent quality assurance without requiring additional human reviewers.

## Update/Rollback Compatibility

The implementation is designed to be non-breaking and reversible:

- **Non-breaking rollout**: Warnings first, requirements later allows gradual adoption
- **Configurable rules**: Rules can be adjusted based on community feedback
- **Disable capability**: Checks can be disabled if issues arise
- **Backward compatible**: Existing commit messages remain valid
- **Grace periods**: Allow time for community to adapt to new requirements

If needed, the framework can be rolled back by:
- Disabling GitHub Actions workflows
- Removing branch protection requirements
- Reverting configuration files

## Functional Testing Approach

The framework will be tested through:

- **Sample commit messages**: Test linting with various commit message formats
- **PR integration**: Test GitHub Actions integration and PR checklist updates
- **Edge cases**: Test with edge cases like very long commits, complex VEPs, and unusual formats
- **Performance testing**: Ensure checks complete in reasonable time
- **False positive analysis**: Monitor and reduce false positives in AI analysis

Testing will be iterative, with refinements based on real-world usage and community feedback.

## Implementation History

To be filled as implementation progresses.

## Graduation Requirements

### Alpha

- [ ] Commit message linter integrated via GitHub Actions
- [ ] Basic commit message format rules defined and documented
- [ ] PR checklist warnings functional and visible to contributors
- [ ] Configuration files committed to repository
- [ ] Documentation on commit message format available
- [ ] Community feedback collected and incorporated
- [ ] Tool selection finalized based on evaluation

### Beta

- [ ] AI context file created (CLAUDE.md or similar) with repository standards
- [ ] AI analysis integrated via GitHub Actions
- [ ] VEP content validation working against template requirements
- [ ] AI analysis provides actionable feedback on PRs
- [ ] Refined based on Alpha feedback and usage patterns
- [ ] Documentation updated with AI analysis capabilities
- [ ] Privacy and security considerations addressed
- [ ] Performance validated with real-world usage

### GA

- [ ] Quality checks made required GitHub status checks
- [ ] Branch protection rules configured to require passing checks
- [ ] All documentation complete and accessible
- [ ] Community adoption confirmed through usage metrics
- [ ] Support processes established for common issues
- [ ] Framework proven stable in production use
- [ ] Maintenance plan established for ongoing updates
