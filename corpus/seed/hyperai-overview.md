# HYPER-AI Project Overview

Sources: https://hyper-ai-project.eu/ and the HYPER-AI hackathon pages (captured 2026-10-06).

## What is HYPER-AI

HYPER-AI is an EU-funded research project (Grant Agreement 101135982). Its mission is to revolutionise big-data processing by developing "self-abstracted and self-coordinated cloud-to-edge resources". It addresses the complexity of integrating Internet of Things (IoT), Edge and Cloud computing into one computing continuum, through smart virtual computing nodes that optimise data-processing applications across a distributed network to improve performance and efficiency.

## Four technology pillars

1. Network Resources Abstraction and Functional Registration — efficient resource discovery and organisation across distributed systems.
2. Self-Configuration, Self-Healing, Self-Optimizing Framework — autonomous systems that adapt without manual intervention.
3. Self-Protection Framework — security mechanisms embedded throughout the infrastructure.
4. Open Application Representation and IDE — development tools supporting the computing continuum. The HyperAI IDE belongs to this pillar.

## Use cases (five industrial verticals)

- Mobility and Automotive — utilising idle computing power in connected vehicles.
- Industry 4.0 — augmented reality and AI for remote assembly operations.
- Healthcare — early disease diagnosis and pandemic prevention.
- Green Energy — efficient data processing for infrastructure monitoring.
- Farming and Agriculture — precision agriculture via cloud-to-edge-to-IoT systems.

## Partners and governance

CERTH (Centre for Research and Technology Hellas) coordinates the project. The consortium includes the telecommunications provider Telefónica, the Eclipse Foundation, academic institutions (National and Kapodistrian University of Athens, Cyprus University of Technology), technology companies (eBOS, ENEA, CSEM) and other European organisations.

## The HyperAI IDE

The HyperAI IDE is the integrated development environment provided by HyperAI to help users build, run and inspect AI workflows over the computing continuum. Users describe applications as YAML application profiles (native apps for containers and VMs on swarm nodes, device apps for registered devices such as Android phones, ESP32 boards and Docker-capable edge devices), validate them live, and deploy them from the IDE. Hyperion is the LLM-powered agentic assistant inside the IDE: it answers questions about HYPER-AI and acts on the user's behalf by creating, editing and deleting files.

## Veles Hack 2026

Challenge 1 of the Veles Hack 2026 (Eclipse Foundation, hybrid event in Valencia, 6–8 October 2026) asks participants to build Hyperion: an agentic microservice for the HyperAI IDE that answers questions about HYPER-AI, translates natural language into IDE actions, rejects irrelevant queries, grounds answers in documentation (RAG), keeps session memory and asks for confirmation before state-changing actions.
