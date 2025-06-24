from flask import Flask, request, render_template, redirect, url_for
import pdfplumber
from openai import OpenAI
import os
from collections import defaultdict
app = Flask(__name__)
from datetime import datetime, timedelta
import re

from dotenv import load_dotenv
load_dotenv()


client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    organization=os.getenv("OPENAI_ORG_ID")
)

import re

def is_transaction_line(line: str) -> bool:
    """
    Detects if a line contains a valid transaction based on:
    - Presence of date in supported formats (anywhere in the line)
    """

    DATE_REGEX = (
    r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'                                     # 1/1/2024 or 01-01-2024
    r'|^\d{1,2}[.]\d{1,2}[.]\d{2,4}'                                      # 01.01.2024
    r'|^\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[,]?\s+\d{2,4}'  # 01 Jan 2024 or 1 Jan, 24
    r'|^\d{1,2}[-](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-]\d{2,4}'      # 01-Jan-24
    r'|^\d{4}[-/]\d{2}[-/]\d{2}'                                          # 2024-01-01
)

    # Normalize spacing
    line = re.sub(r'\s{2,}', ' ', line.strip())

    # Check if line contains a date pattern anywhere
    return re.search(DATE_REGEX, line, flags=re.IGNORECASE) is not None



from datetime import datetime
import re
from collections import defaultdict

def extract_last_transaction_on_or_before_day(full_text: str, target_day: int = 5, max_months: int = 6):
    """
    For each of the first max_months months:
    - If there are transactions on the target_day, pick the last one.
    - Otherwise, pick the latest transaction before the target_day.
    - Skip the month if no transaction on or before the target_day.
    """
    lines = full_text.splitlines()

    date_pattern = re.compile(
        r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|'
        r'(\d{1,2}[.]\d{1,2}[.]\d{2,4})|'
        r'(\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[,]?\s+\d{2,4})|'
        r'(\d{1,2}[-](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-]\d{2,4})|'
        r'(\d{4}[-/]\d{2}[-/]\d{2})',
        flags=re.IGNORECASE
    )

    possible_formats = [
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", 
        "%d-%m-%y", "%d/%m/%y", "%d.%m.%y",
        "%d-%b-%Y", "%d %b %Y", "%d %B %Y",
        "%Y-%m-%d", "%d-%b-%y", "%d %b %y"
    ]

    date_line_map = []
    for line in lines:
        line = re.sub(r'\s{2,}', ' ', line.strip())
        match = date_pattern.search(line)
        if match:
            date_str = match.group(0)
            for fmt in possible_formats:
                try:
                    date_obj = datetime.strptime(date_str, fmt)
                    date_line_map.append((date_obj, line))
                    break
                except ValueError:
                    continue

    grouped = defaultdict(list)
    for dt, line in date_line_map:
        grouped[(dt.year, dt.month)].append((dt, line))

    selected_lines = []
    for (year, month), entries in sorted(grouped.items())[:max_months]:
        try:
            target = datetime(year, month, target_day)
        except ValueError:
            # Invalid day for this month (e.g., Feb 30)
            continue

        valid_entries = [e for e in entries if e[0] <= target]
        if not valid_entries:
            continue

        valid_entries.sort(key=lambda x: x[0])
        last_entry = valid_entries[-1]  # 👉 last transaction on or before target day
        selected_lines.append(last_entry[1])

    return selected_lines


def clean_pdf_text(full_text: str) -> str:
    """
    Extracts likely transaction rows from bank statement text and formats
    them for use with GPT. Removes noise, normalizes structure.
    """
    lines = full_text.splitlines()
    cleaned_rows = []

    for line in lines:
        if is_transaction_line(line):
            cleaned_rows.append(line.strip())

    if not cleaned_rows:
        return 0

    # Format for GPT - Markdown-style table
    output = "Below is the list of bank transactions. Each line contains a date, description, amount(s), and closing balance:\n\n"
    output += "\n".join(cleaned_rows)
    print("sayantan")
    return output




@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'pdf_file' not in request.files:
            return 'No file part', 400

        files = request.files.getlist('pdf_file')  # ✅ Get multiple files
        if not files or all(f.filename == '' for f in files):
            return 'No selected file', 400

        # Get the target day from the form input (default to 5 if blank or invalid)
        try:
            target_day = int(request.form.get('target_day', 5))
            if target_day < 1 or target_day > 31:
                target_day = 5
        except ValueError:
            target_day = 5


        full_text = ""
        os.makedirs("temp", exist_ok=True)

        for file in files:
            pdf_path = os.path.join("temp", file.filename)
            file.save(pdf_path)

            # Extract text from each PDF
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text()
                    if text:
                        full_text += f"--- {file.filename} | Page {i} ---\n{text}\n\n"

        # Clean and format
        formatted_text = clean_pdf_text(full_text)
        if formatted_text == 0:
            formatted_text = full_text
        filtered_text = extract_last_transaction_on_or_before_day(formatted_text, target_day=target_day)
        
        
        gpt_result = ""
        if isinstance(filtered_text, list):
            filtered_text = "\n".join(filtered_text)
        
        # 🔁 OpenAI API call
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                temperature=0,  # or gpt-4o if you have access
                messages=[
                    {
                        "role": "system",
                        "content": "You are a financial assistant that analyzes bank statements."
                    },
                    {
                "role": "user",
"content": f"""
Here is the extracted bank statement:
{filtered_text}

Each line contains a transaction that ends with the **closing balance**.
This balance is always the **last numeric value in the line**, typically followed by "CR" or "DR" (but not always).

Please follow these rules:
1. For each line:
   - Extract the **transaction date** in the format `DD-MM-YYYY`.
   - Extract the **closing balance**, which is the **last numeric value** in the line (before or followed by 'CR' or 'DR').
2. Treat each line as the **final selected transaction for a month**.
3. Format the output as:
   - Used date: `DD-MM-YYYY`, Closing Balance: ₹amount
4. After listing all lines, calculate the average of the extracted closing balances and display it as:
   **average_balance = ₹amount**

⚠️ Output only as specified. Do not include any summaries, titles, headers, or additional explanations.
"""
            }
                ]
            )
            gpt_result = response.choices[0].message.content
        except Exception as e:
            gpt_result = f"❌ OpenAI Error: {str(e)}"
        
        
        
        return render_template(
            'result.html',
            formatted_text=formatted_text or "",
            filtered_text=filtered_text or "",
            gpt_result=gpt_result or ""
        )

    return render_template('upload.html')



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000,debug=True)
