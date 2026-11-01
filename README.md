EpiScope — Public Health Surveillance and Outbreak Detection
1. Project Overview
EpiScope is a public health surveillance and analytics platform designed to process disease surveillance data, monitor disease trends, detect potential outbreaks, and support healthcare resource planning across districts in Bangladesh.
The system implements a data engineering pipeline using Bronze, Silver, and Gold layers, statistical outbreak detection algorithms, forecasting models, and an interactive dashboard for visualizing public health indicators.
2. Project Objectives
Build a reliable and reproducible public health data processing pipeline.
Clean, validate, and enrich disease surveillance records.
Aggregate disease cases by district, disease, and week.
Detect potential outbreaks using Rolling Z-score and EARS-C2 algorithms.
Evaluate outbreak detection performance using sensitivity, precision, and detection delay.
Forecast future disease cases and estimate healthcare resource demand.
Visualize disease trends, alerts, and district-level statistics through a dashboard.
3. System Architecture
<p align="center">
  <img src="docs/System_arcitecture.png" width="800" alt="Project Dashboard">
</p>
4. Technologies Used
Technology
Purpose
Python
Data processing and analytics
Pandas and NumPy
Data cleaning and statistical calculations
PySpark
Scalable data processing and aggregation
Parquet
Storage of structured pipeline data
FastAPI
Backend API and data delivery
D3.js
Interactive charts and district maps
HTML, CSS and JavaScript
Dashboard interface
Scikit-learn
Machine learning and forecasting
PuLP
Resource allocation optimization
Git and GitHub
Version control and collaboration

5. Project Structure
episcope/
├── analytics/
│   ├── calendar/
│   │   ├── outbreak_calendar.py
│   │   └── preparedness_calendar.py
│   ├── detection/
│   │   ├── ears_alerts.py
│   │   ├── ears_c2.py
│   │   └── evaluate_detection.py
│   ├── forecast/
│   │   ├── forecast_cases.py
│   │   ├── resource_demand.py
│   │   ├── seir_model.py
│   │   └── evaluate_forecasts.py
│   ├── optimise/
│   │   ├── allocation.py
│   │   ├── equity_analysis.py
│   │   └── scenario_analysis.py
│   └── scalability/
├── api/
│   └── main.py
├── dashboard/
│   ├── app.js
│   ├── index.html
│   └── d3.v7.min.js
├── data/
│   ├── raw/
│   ├── lake/
│   │   ├── bronze/
│   │   ├── silver/
│   │   └── gold/
│   ├── quarantine/
│   ├── reference/
│   ├── reports/
│   └── weather/
├── requirements.txt
└── README.md
Note: The structure above highlights the main project components. Actual files and directories may vary by version.
6. Installation and Execution Instructions
Prerequisites
Python 3.11 (or the version supported by the project)
Git
A Windows, Linux, or macOS environment
Required Python dependencies
Step 1: Clone the repository
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd episcope
Step 2: Create a virtual environment
Windows PowerShell:
python -m venv .venv311
.\.venv311\Scripts\Activate.ps1
Step 3: Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
Step 4: Run outbreak detection
python .\analytics\detection\ears_alerts.py
python .\analytics\detection\ears_c2.py
Step 5: Evaluate outbreak detection
python .\analytics\detection\evaluate_detection.py
The evaluation outputs are saved under analytics/detection/.
Step 6: Run forecasting and resource planning
Run the forecasting script first:
python .\analytics\forecast\forecast_cases.py
After verifying that the forecast output exists, run resource-demand estimation:
python .\analytics\forecast\resource_demand.py
Additional allocation and equity analysis scripts can be executed after their required input files have been generated.
Step 7: Start the API server
From the project root, use the following command if api/main.py exposes an ASGI application named app:
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
Open the API documentation at:
http://127.0.0.1:8000/docs
Step 8: Open the dashboard
If the dashboard is served by the FastAPI application, open:
http://127.0.0.1:8000/
Otherwise, follow the dashboard's configured static-file serving instructions.
7. Team Members and Contributions
The EpiScope project was developed by three team members, each responsible for a major component of the system.
Team Member
Role
Contribution
Mahitun Nesa Mahi
Data Engineer
Data collection, Spark pipeline, Bronze/Silver/Gold layers, data cleaning, quality, and pipeline performance
Riya Biswas
Analytics Engineer
Outbreak detection, evaluation, forecasting, resource-demand estimation, and allocation optimisation
Md Saiful Alam
Visualization Engineer
API integration, D3.js dashboard, charts, maps, usability testing, and project demonstration


8. Key Features
Layered Bronze, Silver, and Gold data architecture.
Data validation, deduplication, and quarantine handling.
District-level disease aggregation.
Rolling Z-score and EARS-C2 outbreak detection.
Detection performance evaluation.
Disease forecasting and illustrative resource-demand estimation.
District-level maps, trend charts, alerts, and summary indicators.
9. Data Quality and Reliability
EpiScope uses data validation and quarantine mechanisms to separate invalid records from accepted records. Aggregated Gold datasets provide structured inputs for analytics and dashboard reporting.
Pipeline execution reports and detector evaluation outputs support reproducibility and quality assessment. Data quality percentages should be calculated from verified input, accepted, and quarantined record counts.
10. Important Notes
Outbreak alerts are statistical signals and should not be interpreted as confirmed outbreaks.
Forecasts depend on data availability, model assumptions, and validation results.
Resource-demand estimates are planning estimates, not clinical recommendations.
Sensitive or restricted health data should not be published in the repository.
Large datasets, virtual environments, secrets, and generated files should be excluded from Git when appropriate.
11. Video Presentation Link
12. Video Chapter Timestamp

EpiScope — Turning public health data into actionable surveillance insights.

Before pushing to GitHub
Check these three things:
Replace <YOUR_GITHUB_REPOSITORY_URL> with your actual repository URL.
Add the real team members' names and contributions.
Verify that api/main.py, requirements.txt, and the dashboard startup instructions match your current project.
Then save the document as README.md in E:\episcope and run:
git add README.md
git commit -m "Add project README documentation"
git push
This will publish the README with your next Git push.

