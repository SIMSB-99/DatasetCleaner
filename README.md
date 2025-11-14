# DatasetCleaner  
*A Python-based interactive tool for reviewing, labeling, and curating large image datasets.*

DatasetCleaner is a **local-first**, Streamlit application that enables researchers to efficiently review and clean large image datasets. It offers a structured workflow for inspecting each image, its metadata, and assigning one of three decisions: **keep**, **discard**, or **unsure**.

The tool also includes an Explorer interface modeled after Windows Explorer, allowing users to navigate large datasets by contextual metadata (e.g., locations) for rapid visual skimming.


## Installation

### 1. Clone the repository

```bash
git clone https://github.com/SIMSB-99/DatasetCleaner.git
cd DatasetCleaner
```

### 2. Create a virtual environment (venv or conda)
#### 2.1 venv
```bash
python -m venv venv
venv\Scripts\activate             # Windows
```
#### 2.2 (conda)
```bash
conda activate your_env
```

### 3. Install dependecies
```bash
pip install -r requirements.txt
```

### 4. Set database path (open terminal in the window with the code)
```bash
set IMGQA_DB_PATH=.\image_qa.sqlite
```

### 5. Run the app
```bash
streamlit run app.py
```


The app opens at http://localhost:8501/

## Dataset ingestion

### 1. Set dataset name (e.g., VLM4Context)
### 2. Select dataset's root directory
### 3. Upload metadata CSV
### 4. Click "Ingest"