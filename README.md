# IPO Monitoring System

A full-stack application for tracking and automating initial public offering (IPO) data. The system uses a distributed architecture to scrape financial data, process it in the background, and serve it through a high-performance frontend.

## System Architecture

The project is built with a decoupled architecture to ensure scalability and reliability:

* Frontend: Next.js (React) application using Server-Side Rendering (SSR) and Incremental Static Regeneration (ISR).
* API: Django REST Framework (DRF) serving as the centralized data gateway.
* Task Queue: Celery with Redis as the message broker for background data ingestion.
* Database: PostgreSQL for persistent storage of IPO records and system logs.
* Orchestration: Docker Compose manages service discovery and networking across five distinct containers.



## Key Technical Features

### Background Data Processing
The system offloads external API communication to Celery workers. This prevents the web server from hanging while waiting for responses from the Finnhub API. A Celery Beat scheduler is configured to trigger updates automatically at 08:00 daily.

### Performance and Caching
The frontend utilizes Next.js revalidation. Instead of querying the database for every user request, the system serves cached data. When the background worker completes a sync, it triggers a webhook to purge the cache and update the UI.

### Reliability and Security
* Mocked Testing: The test suite uses the Python mock library to simulate API responses, allowing for offline testing and consistent CI/CD results.
* Rate Limiting: API throttling is implemented at the Django level to prevent excessive resource consumption.
* Service Discovery: Internal services communicate over a private Docker bridge network, keeping the database and Redis hidden from the public internet.

## Installation and Setup

### Prerequisites
* Docker and Docker Compose
* Finnhub API Key

### Execution
1. Clone the repository.
2. Create a .env file in the root directory based on .env.example.
3. Build and start the containers:
   docker-compose up -d --build
4. Access the dashboard at http://localhost:3000.
