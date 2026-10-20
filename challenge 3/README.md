# Challenge 3: Generative AI for Data Analysis Support

## Challenge Description

Hollandse Luchten collects large amounts of sensor data on air quality. However, analysing and interpreting this data can be difficult. It often requires specific expertise in air quality, particulate matter, and data analysis.
This challenge seeks solutions that enable residents to explore and analyse air-quality data themselves, even if they do not have this specialist knowledge. The key objective is to make data analysis more accessible while ensuring that results remain valid, understandable, and transparent.
The core idea: Residents ask their questions naturally, the agent/tool interprets the request, uses available data analysis tools to answer it, and provides results along with a complete audit trail showing which steps were taken.


## Problem Statement

Residents who want to understand air quality in their area can use existing tools (https://analyseren.samenmeten.nl/) to select sensor data, filter possible outliers, and create visualizations such as pollution roses, bar charts, and time-series plots. However, using these tools still requires users to understand data-analysis concepts and make methodological choices.
This challenge is to develop a solution that makes air-quality data analysis more accessible to residents. The solution should help users formulate and investigate their own questions, guide them through appropriate analytical steps, and explain the results in clear language.
The solution should also make the analysis transparent. Users need to be able to see which data was used, which choices were made, which methods were applied, and how these choices may affect the outcome. This enables residents to carry out analyses independently in a valid, understandable, and reproducible way.
A solution may, for example:
1.	**Help** residents turn a question into an appropriate analysis.
2.	**Support** the selection of relevant sensor data, locations, and time periods.
3.	**Guide** users in making methodological choices, such as filtering outliers or comparing groups.
4.	Create clear and useful **visualizations**.
5.	**Explain** results, assumptions, limitations, and uncertainty in accessible language.
6.	Provide a **transparent record** of the data, methods, decisions, and steps used.
7.	**Bonus:** Support follow-up questions and refinements.


## Possible techniques

Teams may combine one or more of the following techniques:
Large Language Models: Use models such as GPT, Claude, or similar where they add clear value in understanding residents’ questions and translating them into an analysis request. If other means than LLMs provide a sufficient solution, this is encouraged. Prefer simpler, less resource-intensive approaches when these meet the requirements with comparable reliability and transparency.
Tool integration: Enable the agent to select and call suitable data-analysis functions, for example for filtering data, comparing groups, or creating visualisations.
Planning and reasoning: Break complex questions into clear analysis steps that can be reviewed and reproduced.
Retrieval-Augmented Generation (RAG): Retrieve relevant information about available datasets, sensor metadata, analysis methods, and tool documentation.
Structured and auditable output: Produce consistent results that show the answer, data used, analysis steps, assumptions, and limitations.
A simple, well-validated approach may be more valuable than a complex solution that is difficult to explain or verify. Teams should be able to substantiate their technical choices and considerations to the jury, for example with validation metrics, test cases, research, or comparisons with reference measurements.


## Which data is available?

During the hackathon we will supply you with several datasets for this challenge:
-	Time-series data: hourly PM2.5 from more than 100 Hollandse Luchten sensors, some ranging several years.
-	Reference measurements: Official reference station data producing hourly measurements, to possibly compare the Hollandse Luchten sensors with.
-	Metadata on sensors: information about the sensor location, type, measurement communities, and intended reference stations.
-	Weather data from the Dutch KNMI, for example temperature and humidity.
-	Github codebase Samen Analyseren consisting of R code: https://github.com/rivm-syso/Analyse-Together


## Deliverables

Your submission should at least contain:
1)	The Github repository with any used code and/or examples.
2)	The presentation of your solution.
3)	A concrete example showing how the system handles a question and provides an audit trail explaining how it arrived at the answer.
Every team is expected to present their solution to our jury.


## Jury criteria
| Criterion | What the jury looks for |
|---|---|
| 1. Relevance to the challenge | To what extent does the solution address a concrete problem from the chosen challenge? The team explains who the solution is intended for and why it is relevant to Hollandse Luchten. |
| 2. Technical quality and reliability | The solution is logically designed and demonstrably works. The team justifies its chosen method, algorithm, or model: why is this choice appropriate for the task and the available data? The team also explains its trade-offs, for example through references to literature, comparisons with alternatives, or its own validation and reliability measurements using data-science methods. Assumptions, uncertainties, and limitations are made clear. |
| 3. Value for residents | The solution helps residents better understand, analyse, or use air-quality data. The jury considers clarity, accessibility, and practical value: what can residents do or learn with it? |
| 4. Innovation and smart application | The solution takes an original or surprising approach. Innovation may lie in the technical solution, the combination of data sources, how results are presented, or how residents are supported. The approach must remain appropriate and practical. |
| 5. Feasibility and presentation | The team clearly demonstrates how the solution works and what is needed to develop it further. The jury considers whether the demonstration or explanation is convincing, whether next steps are realistic, and whether practical requirements such as data availability, maintenance, privacy, and real-world use have been considered. The jury assesses the extent to which the solution is feasible to implement and aligns with the existing technical, social, and organisational structures of Hollandse Luchten. |


## Use of tools and generative AI

AI (coding) assistants and vibe coding are allowed, as long as teams disclose clearly on where they did and did not use them. We encourage the use of such tools, but also address that teams should be able to explain and validate important parts of their solutions themselves.  


## metadata
The sensors of hollandse luchten will inculde the following:
date
location
sensortype
pm2.5 or pm10
calibrated or raw.

The parquet file contains the following columns: `station`, the name of the station or sensor; `date`, the date and time in the Europe/Amsterdam time zone when the hour ends; `wd`, the wind direction (missing data: -999; calm: 0; variable: 990); `ws`, wind speed in m/s; `pm10`, PM10 concentration in μg/m³; `pm10_kal`, calibrated PM10 concentration in μg/m³; `pm25`, PM2.5 concentration in μg/m³; `pm25_kal`, calibrated PM2.5 concentration in μg/m³; `lat`, the location’s latitude; and `lon`, the location’s longitude. Unavailable values are marked as `NA`.



## Resources

# Hollandse Luchten Hackathon Resource Pack

## Start here


| Link | What participants will find |
|---|---|
| [Hollandse Luchten – homepage](https://hollandse-luchten.org/) | General introduction to the citizen measurement network, its goals, and the regions where measurements take place. |
| [Welcome](https://hollandse-luchten.org/welkom/) | Overview of the active measurement areas, including IJmond, Zaanstad and Amsterdam Noord. |
| [Frequently asked questions](https://hollandse-luchten.org/faq/) | The key facts in one place: which sensors are used, which substances are measured, where the open data is available, and how to view historical data. |
| [Measurement results](https://hollandse-luchten.org/meetresultaten/) | Interactive map with current particulate matter measurements, sensors, historical data, and information about open data. |

## Sensors and hardware

| Link | What participants will find |
|---|---|
| [Sensors](https://hollandse-luchten.org/sensoren/) | Official explanation of the current SODAQ AIR, open hardware/data, installation, status lights, and maintenance. |
| [Handbook for starting your measurement group (PDF)](https://hollandse-luchten.org/wp-content/uploads/sites/9/Hollandse-Luchten-Handboek-september-2022.pdf) | Detailed guide covering sensor technology, particulate matter measurement, sensor placement, data visualisation, and the transition from HoLu kits to SODAQ AIR. |
| [A look back at the first phase](https://hollandse-luchten.org/updates/een-terugblik-op-de-eerste-fase-van-hollandse-luchten-wat-zijn-de-belangrijkste-lessen-en-waar-zijn-we-trots-op/) | Background on the first 200 HoLu sensor kits: modular build kit, PM2.5/PM10, and NO2 and ozone in the extended version. |
| [Train the trainer](https://hollandse-luchten.org/updates/hollandse-luchten-train-the-trainer/) | Materials and context about citizen measurements, air quality, and sensors; useful for teams building an onboarding or educational tool. |

## Data, analysis and tools

| Link | What participants will find |
|---|---|
| [Analyse the data with the Samen Analyseren Tool](https://hollandse-luchten.org/updates/analyseer-de-hollandse-luchten-data-met-de-samen-analyseren-tool/) | Explanation of how to select, visualise, and analyse Hollandse Luchten data, including combinations with Luchtmeetnet and KNMI weather data. |
| [Manual for the Samen Analyseren Tool](https://hollandse-luchten.org/updates/handleiding-beschikbaar-voor-de-samen-analyseren-tool/) | Introduction to the RIVM data portal and how to view variation in air quality over the past seven days. |
| [New version of the Samen Analyseren Tool](https://hollandse-luchten.org/updates/vernieuwde-versie-van-de-samen-analyseren-tool/) | Update about the interactive data-visualisation tool for sensor data. |
| [Getting started with data in Haarlem](https://hollandse-luchten.org/updates/aan-de-slag-met-data-in-haarlem/) | Practical workshop approach: checking sensor availability, comparing sensor groups, finding weekday patterns, and including wind direction. |
| [Source identification with the dashboard](https://hollandse-luchten.org/updates/hoe-kun-je-met-het-hollandse-luchten-dashboard-onderzoek-doen-naar-bronbepaling/) | Step-by-step approach for source investigation using sensor data, official LML data, KNMI weather data, and other sources. |
| [Updates](https://hollandse-luchten.org/updates/) | News archive with software updates, API status, calibrations, GGD research, and tool changes. |

## Data quality, calibration and validation

| Link | What participants will find |
|---|---|
| [In-depth data analysis: measuring industry (PDF)](https://hollandse-luchten.org/wp-content/uploads/sites/9/HollandseLuchten_VerdiependeDataAnalyse_IndustrieMeten.pdf) | Technical analysis presentation: checking individual sensors, temperature and wind effects, response functions, and differences between sensors. |
| [Research](https://hollandse-luchten.org/onderzoek/) | Overview of research into data quality, calibration, GGD validation, and new measurement techniques. |
| [Calibration of the Sensirion SPS30 on the map](https://hollandse-luchten.org/updates/) | Via the updates overview: RIVM calibration for the SPS30 sensor and corrected PM2.5 data on the map. |
| [GGD research into SODAQ AIR](https://hollandse-luchten.org/updates/) | Via the updates overview: reliability research placing SODAQ AIR and Snuffelfiets devices next to official GGD monitors in Zaandam and Beverwijk. |

## Particulate matter and interpretation

| Link | What participants will find |
|---|---|
| [Particulate matter and health](https://hollandse-luchten.org/updates/fijnstof-en-gezondheid/) | Explanation of PM2.5, health effects, the limitations of low-cost sensors, and how to interpret measurements correctly. |
| [Citizen science and measurement groups](https://hollandse-luchten.org/burgerwetenschap-en-meetgroepen/) | Explanation of particulate matter dispersion, SODAQ AIR, Palmes tubes, and the difference between continuous sensor measurements and four-weekly NO2 measurements. |
| [Health-effects logbook](https://hollandse-luchten.org/updates/logboek-om-gezondheidseffecten-in-kaart-te-brengen/) | Method and idea for linking health observations to sensor measurements or particulate matter peaks. |

## Locations and case studies

| Link | What participants will find |
|---|---|
| [Haarlem category](https://hollandse-luchten.org/category/haarlem/) | Examples of local data analyses, including identifying possible source locations based on particulate matter patterns. |
| [Zaanstad / Zaanse Schans context](https://hollandse-luchten.org/) | Example of a local measurement question: the impact of tourism on particulate matter and NO2 around the Zaanse Schans. |

## Important for participants

- The current **SODAQ AIR** measures PM2.5, temperature, and humidity.
- The older **HoLu kits** measured PM2.5 and PM10; the extended version also measured NO2 and ozone.
- NO2 is measured using **Palmes diffusion tubes**, which are read approximately once every four weeks.
- SODAQ AIR takes measurements every few minutes.
- Open data is available through the Hollandse Luchten map, **Samen Meten**, SODAQ **Knowyourair**, and the **API of the Province of North Holland**.
- Treat sensor data as indicative: always check individual sensors and compare results with reference stations or corrected data.



## Sample Question Types

To help guide development, consider supporting these question categories:

1. **Temporal Comparison**: "How does January compare to December for air quality?"
2. **Location Analysis**: "Which sensor has the worst air quality in our neighborhood?"
3. **Trend Analysis**: "Is air quality improving or getting worse this year?"
4. **Anomaly Detection**: "Were there any unusual air quality events last month?"
5. **Comparative Analysis**: "Is air quality better on weekends vs. weekdays?"
6. **Source Investigation**: "Which direction does the pollution come from when it's bad?"
7. **Statistical Summaries**: "What's the average air quality in our area?"
8. **Alerts/Thresholds**: "When did air quality exceed safe levels?"
9. **Forecasting Support**: "Based on current trends, how will next week's air quality look?"
10. **User Education**: "What factors influence air quality measurements?"

## Tips / links

- Start with a small set of well-defined questions and gradually expand
- Use concrete example analyses from Samen Analyseren as templates
- Test with actual residents to understand how they naturally phrase questions
- Build a fallback system for questions you can't handle ("I don't know how to answer that")
- Consider context from previous questions (conversational memory)
- Prioritize clarity and correctness over trying to answer everything
- Make the audit trail visually appealing and easy to understand
- Start with simple tools before adding complex analysis chains
- Use prompt engineering to guide the agent's reasoning
- Consider using few-shot learning with examples to improve performance


## openrouter and other API keys

### Quick start: test an agent with OpenRouter
Oprouter free offers 50 requests/day. Use this if needed for testing and demoing your solution.
OpenRouter provides an OpenAI-compatible API that can connect an agent to models from different providers.

1. Create an account at [OpenRouter](https://openrouter.ai/).
2. Create an API key on the [Keys page](https://openrouter.ai/keys). Treat the key like a password: store it in an environment variable or a local `.env` file, and never commit it to Git or share it publicly.
3. Choose a model marked as free on the [Models page](https://openrouter.ai/models). Free models and their availability, rate limits, and usage conditions can change; check the current details before testing.
4. In your agent framework, select the OpenAI-compatible provider and configure the API key, base URL, and chosen model:

	| Setting | Value |
	|---|---|
	| API key | Your `OPENROUTER_API_KEY` value |
	| Base URL | `https://openrouter.ai/api/v1` |
	| Model | The exact model ID shown on OpenRouter |

Start with a small test prompt and check the model's current usage limits. Requests are sent to OpenRouter and the selected model provider, so do not include secrets, personal information, or other sensitive data. A free model may have limited capacity or may not be suitable for every task; verify an agent's analysis against the underlying data and include its limitations in the audit trail.




## Copyright and permission to use


Copyright for the original code and materials in this repository remains with their respective authors, unless otherwise agreed in writing. The authors grant the Hollandse Luchten consortium and its partners a non-exclusive, royalty-free permission to use, reproduce, modify, adapt, and integrate this solution for evaluating, developing, maintaining, and operating tools and services related to Hollandse Luchten. The consortium may build upon the solution for these purposes. This permission does not transfer ownership of the original work.

This permission applies only to material created by the authors. Third-party software, datasets, text, images, and other materials remain subject to their own copyright, licence terms, and usage conditions. Any required attribution must be retained. Contributors should confirm that they have the rights to grant this permission for the material they contribute.

Before relying on this statement as a formal licence, the team and the consortium should confirm that its scope and wording meet their needs.

---

[Back to main README](../README.md)
