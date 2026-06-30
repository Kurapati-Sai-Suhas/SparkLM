# Software Requirements Specification (SRS)
**Project Name:** LearnLM (Intelligent Virtual Study Group Platform)  
**Version:** 1.0  
**Role:** Lead AI & Backend Architecture  

---

## 1. Introduction

### 1.1 Purpose
The purpose of this document is to define the software requirements for LearnLM, an intelligent, cloud-hosted collaborative learning platform. This document outlines the system architecture, AI/ML integrations, API constraints, and cloud infrastructure which is required to build the platform.

### 1.2 Intended Audience
This document is intended for frontend developers, backend engineers, AI/ML engineers, and technical recruiters reviewing the project architecture. 

### 1.3 Project Scope
LearnLM moves beyond standard file-sharing platforms by integrating deep learning and adaptive algorithms. It features collaborative workspaces, a "Visual Semantic Search" engine for retrieving un-tagged diagram notes using Deep Learning, and an "Adaptive Coding Portal" that uses a Hybrid AI Engine to personalize learning paths based on the prerequisite structure of the subject.

---

## 2. Overall Description

### 2.1 Product Perspective
LearnLM is a distributed web application. It consists of a React/TypeScript frontend (Single Page Application) and a Python/Django backend. The backend serves as a REST API and a "Traffic Cop" that routes requests to various Machine Learning models, external code-execution sandboxes, and cloud storage systems.

### 2.2 Operating Environment
* **Frontend:** Modern Web Browsers (Chrome, Firefox, Safari).
* **Backend:** Python 3.x, Django REST Framework.
* **Database:** Azure Cosmos DB (MongoDB API).
* **Cloud Host:** Microsoft Azure (App Service, Static Web Apps, Blob Storage).

### 2.3 Design and Implementation Constraints
* **Sandboxed Execution:** User-submitted code must NEVER run on the main Django server. It must be isolated via an external API (Judge0/Piston).
* **AI Latency:** Deep learning inferences (feature extraction, graph traversals) introduce latency. The frontend must implement robust loading states.
* **CORS:** Strict Cross-Origin Resource Sharing rules must be configured to allow communication between the React frontend and Django backend.

---

## 3. System Features & Functional Requirements

### 3.1 Module A: Collaborative Workspaces & User Management
* **REQ-1.1:** The system shall allow users to register, log in, and maintain a secure session using JWT (JSON Web Tokens).
* **REQ-1.2:** Users shall be able to create isolated "Study Groups."
* **REQ-1.3:** The system shall support Role-Based Access Control (RBAC) within groups (e.g., Admin, Member).
* **REQ-1.4:** Users shall be able to upload study materials (PDFs, Images, Code snippets) to their specific group workspace.

### 3.2 Module B: Visual Semantic Search (Diagram Matcher)
* **REQ-2.1 (Feature Extraction):** Upon image upload, the Django backend shall pass the image through a headless pre-trained Convolutional Neural Network (MobileNetV2) to extract a 1D feature vector representing the image's visual structure.
* **REQ-2.2 (Vector Storage):** The system shall store this massive feature vector array in the NoSQL database alongside the document's metadata.
* **REQ-2.3 (Cosine Similarity Engine):** Users shall be able to upload a cropped image (e.g., a diagram) as a search query. The system will extract its vector and use Cosine Similarity math to find the closest matching vectors in the database, returning the original source documents.

### 3.3 Module C: Adaptive Coding Portal
* **REQ-3.1 (In-Browser IDE):** The frontend shall integrate an editor (like Monaco Editor) supporting syntax highlighting and auto-indentation for Java and Python.
* **REQ-3.2 (Isolated Execution):** When code is submitted, the backend shall route the raw code and hidden test cases to a Judge0/Piston API sandbox and await the Pass/Fail result and execution time.
* **REQ-3.3 (Data Logging):** The system shall log every submission attempt, including execution time, memory usage, and success rate, to build a historical user profile.

### 3.4 Module D: The Hybrid AI Recommendation Router
* **REQ-4.1 (Topic Tagging):** All subjects in the database shall be tagged with a `structure_type` (either `hierarchical` or `flat`).
* **REQ-4.2 (The Traffic Cop):** When a user requests the "next question," the backend router shall check the subject's structure tag to determine the recommendation engine.
* **REQ-4.3 (Hierarchical Engine - GNN):** If the topic is structured (e.g., Data Structures & Algorithms), the router shall query a Graph Neural Network (or Graph Database representation) to recommend questions based on prerequisite knowledge, providing explainable feedback.
* **REQ-4.4 (Flat Engine - Elo/IRT):** If the topic is unstructured (e.g., Tech Trivia), the router shall utilize an Item Response Theory or Elo Rating algorithm to dynamically adjust the difficulty based strictly on the user's current score.

---

## 4. External Interface Requirements

### 4.1 User Interfaces
* Clean, responsive UI built with React.
* Dashboards displaying user rating progression (Elo scores) and topic mastery.

### 4.2 Software Interfaces
* **Judge0 / Piston API:** For secure code compilation and testing.
* **Azure SDKs:** `azure-storage-blob` for image uploads, and `pymongo` to connect to Cosmos DB.

---

## 5. Non-Functional Requirements

### 5.1 Performance
* Standard REST API calls should resolve in < 200ms.
* AI Inference calls (Visual Search, Code Compilation) should resolve in < 3000ms.

### 5.2 Security
* User passwords must be hashed (Argon2 or bcrypt) before database insertion.
* All API endpoints handling user data must require a valid Bearer Token.
* Direct file uploads must be sanitized to prevent malicious script injection before being sent to Azure Blob Storage.

### 5.3 Scalability
* The AI models must be configured to run efficiently on standard CPU cloud instances (PaaS) without requiring dedicated GPU infrastructure, ensuring cost-effective scaling via Azure App Service.

---

## 6. System & Cloud Architecture (Microsoft Azure)

### 6.1 Data Flow Diagram
The following diagram illustrates the interaction between the frontend, the Django routing layer, the isolated sandboxes, and the Azure cloud infrastructure:

```text
[ USER (React Frontend) ]
        |
        | (JSON / REST API over HTTPS)
        v
[ AZURE APP SERVICE (Hosting) ]
[      DJANGO REST API        ]
        |
        +-- (Uploads Diagram) ----> [ MobileNetV2 (Feature Extractor) ] 
        |                                       |
        |                                       v
        +-- (Submits Code) -------> [ Judge0 / Piston API Sandbox ]
        |                                       |
        |                                       v
        +-- (Requests Next Q) ----> [ HYBRID AI ROUTER (Traffic Cop) ]
                                                |
                                    +-----------+-----------+
                                    |                       |
                               [ GNN Engine ]         [ Elo Engine ]
                               (Hierarchical)            (Flat)
                                    |                       |
=============================================================================
                                THE CLOUD (AZURE)
        
  [ AZURE BLOB STORAGE ]                    [ AZURE COSMOS DB ]
  (Saves Raw Images/PDFs)                   (Saves Vectors, Profiles, Graph)
