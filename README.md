
# 📞 Dowell Voice Call Automation

This project is a Flask-based web application that allows you to initiate automated voice calls using Twilio. It supports both CSV and Google Sheet inputs and provides real-time tracking of call status, recordings, speech input, and transcripts.

## 🌟 Features

- Upload contacts via CSV or Google Sheets.
- Automated voice calls using Twilio with custom messages.
- Real-time dashboard for monitoring call status.
- Speech gathering and response parsing (Yes / No / Call back later).
- Audio recordings and transcriptions per call.
- Cancel ongoing calls.
- Export results as CSV.

## 🚀 Live Demo

You can access the app at:

```
https://dowell-caller.onrender.com/
```

## 🧰 Tech Stack

- **Backend**: Python, Flask
- **Frontend**: HTML/JS (Jinja2 templating)
- **Telephony**: Twilio Programmable Voice API
- **Data Sources**: CSV or Google Sheets
- **Background Tasks**: ThreadPoolExecutor
- **Auth**: Google Service Account (for Sheets)
- **Deployment**: Render (or any cloud provider)

---

## 📁 Project Structure

```
├── app.py                  # Main Flask application
├── templates/
│   └── index.html          # Frontend dashboard
├── .env                    # Environment variables
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

---

## ⚙️ Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/twilio-caller.git
cd twilio-caller
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create a `.env` file:

```dotenv
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=your_twilio_phone_number
GOOGLE_CREDENTIALS_FILE='{"type": "service_account", ...}'  # JSON string
BASE_URL=https://your-deployment-url.com
```

> 🔐 You can paste the full contents of your Google service account JSON into the `GOOGLE_CREDENTIALS_FILE` variable.

---

## ▶️ Running the App

```bash
python app.py
```

Then open `http://localhost:10000` in your browser.

---

## 📝 Usage Guide

### 1. Upload CSV File

- CSV must contain at least a `phone_number` column.
- Optional: `name`, `message`

### 2. Google Sheet Mode

- Provide a valid Google Sheet ID.
- Sheet must contain `phone_number`, `name`, and `message` columns.

### 3. Monitor Calls

- The dashboard displays:
  - Call SID
  - Phone Number
  - Status (completed, failed, etc.)
  - Speech Response (Yes/No/Call Back Later)
  - Recording (playable audio)
  - Transcription (if available)

### 4. Cancel or Export

- Cancel selected calls using the cancel button.
- Export all results as CSV.

---

## 🧪 Test Data Example

**CSV Format:**

```csv
phone_number,name,message
+1234567890,John,You are invited to our survey.
+1987654321,Alice,Please respond with Yes or No.
```

---
