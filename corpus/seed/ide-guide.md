# HyperAI IDE Guide and Quick Start

Sources: https://ide-tutorial.hyperai.di.uoa.gr/ide-guide/ and https://ide-tutorial.hyperai.di.uoa.gr/quick-start-demo/ (captured 2026-10-06; summaries of the tutorial pages).

## What the HyperAI IDE is

The HyperAI IDE (https://ide.hyperai.di.uoa.gr/) is the integrated development environment provided by HyperAI to help users build, run and inspect AI workflows in the EU-funded HYPER-AI ecosystem. Users write application profiles in YAML using two specifications: Native Apps (for native applications) and Device Apps (for applications meant to run on a device). The tutorial site has pages: Introduction, Quick Start Demo, IDE Guide, Native Apps Specification, Device Apps Specification, Cookbook & Examples and the Hyperion Agent (Hackathon) page.

## Authentication

Users access the IDE at ide.hyperai.di.uoa.gr and sign in with email and password. New users create an account through the Register link; "Forgot Password?" starts a reset.

## Application Profile Wizard

The Wizard streamlines app definition through either a form interface or a DSL mini-editor that remain synchronized. When you finalize, click Generate YAML — the YAML is automatically sent to the main YAML editor. The main project file is typically `app.yaml`.

## Validation

The IDE provides live validation feedback as users work in the DSL editor, catching configuration errors in real time. Files can also be validated through the backend validation endpoint.

## Settings

The Settings panel lets users adjust the IDE environment: theme, font size and session options, with a reset option.

## Quick Start: deploying a web server

1. Step 0 — Registry preparation: push container images to whitelisted public registries (such as Docker Hub) with standard Docker commands before deployment.
2. Step 1 — Authentication: sign in at the IDE (register if you are new).
3. Step 2 — Workspace organisation: the Workspace Explorer lets users create folders to organise applications.
4. Step 3 — Application configuration: create YAML configuration files defining application attributes, using the Native Apps or Device Apps specification. Example profiles are in the Cookbook.
5. Step 4 — Deployment: save the profile, then press the Deploy button. Several profiles can be selected for deployment at once, with an optional workflow name.
6. Step 5 — Workflow activation: the Dashboard lists deployments; start workflow execution there (it may take a few moments to reach running status).

A Metrics button shows real-time performance analytics of active workflows. Always save profiles before deployment.

## Deployment context

HyperAI targets distributed computing swarms: interconnected autonomous networks that optimise resources across heterogeneous infrastructure (cloud, edge, IoT). Native apps run as containers or VMs on swarm nodes; device apps run on registered devices.
