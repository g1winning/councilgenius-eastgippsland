#!/usr/bin/env python3
"""
CouncilGenius V8 - East Gippsland Shire Council
Production Server using http.server stdlib
"""

import os
import sys
import json
import csv
import hashlib
import re
import time
import logging
import datetime
import urllib.parse
import ipaddress
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import urllib.request

# Configuration
COUNCIL_NAME = "East Gippsland Shire Council"
COUNCIL_DOMAIN = "www.eastgippsland.vic.gov.au"
COUNCIL_PHONE = "(03) 5153 9500"
MODEL = os.getenv("MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 1024
PORT = int(os.getenv("PORT", 8080))
PROMPT_VERSION = "1.0"
BIN_LOOKUP_MODE = "none"
ANTHROPIC_API_KEY = os.getenv(
    "ANTHROPIC_API_KEY",
    ""
)

# Knowledge base path
KB_PATH = Path(__file__).parent / "knowledge.txt"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global state
knowledge_base = ""
knowledge_hash = ""
knowledge_lines = 0
startup_time = time.time()


def load_knowledge_base():
    """Load and hash the knowledge base."""
    global knowledge_base, knowledge_hash, knowledge_lines

    if KB_PATH.exists():
        with open(KB_PATH, 'r', encoding='utf-8') as f:
            knowledge_base = f.read()

        knowledge_hash = hashlib.sha256(knowledge_base.encode()).hexdigest()
        knowledge_lines = len(knowledge_base.split('\n'))
        logger.info(f"Knowledge base loaded: {knowledge_lines} lines, hash: {knowledge_hash}")
    else:
        knowledge_base = ""
        knowledge_hash = hashlib.sha256(b"").hexdigest()
        knowledge_lines = 0
        logger.warning(f"Knowledge base not found at {KB_PATH}")


def filter_pii(text):
    """Filter personally identifiable information from text."""
    # Phone numbers
    text = re.sub(r'\b(?:\d{2}\s?)?\d{4}\s?\d{3}\s?\d{3}\b', '[PHONE]', text)
    text = re.sub(r'\b(?:\d{3}[-.]?)?\d{3}[-.]?\d{4}\b', '[PHONE]', text)

    # Email addresses
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', text)

    # Australian addresses (simplified)
    text = re.sub(r'\b(?:Street|Street|Road|Avenue|Drive|Lane|Court|Crescent|Close|Terrace|Place|Way|Parade)\b', '[ADDRESS]', text, flags=re.IGNORECASE)

    # Names (pattern: Capitalized words)
    text = re.sub(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', '[NAME]', text)

    # Financial account numbers
    text = re.sub(r'\b\d{3}[-]?\d{3}[-]?\d{3}\b', '[ACCOUNT]', text)

    # Australian driver's license and ID patterns
    text = re.sub(r'\b[A-Z]{2}\d{6}\b', '[ID]', text)

    return text


def detect_australian_address(text):
    """Detect if text contains an Australian address."""
    # Australian postcode pattern (0000-9999)
    postcode_pattern = r'\b[0-9]{4}\b'

    # Australian states
    states = ['NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'NT', 'ACT']

    has_postcode = bool(re.search(postcode_pattern, text))
    has_state = any(state in text.upper() for state in states)

    return has_postcode or has_state


def classify(question):
    """Classify question into categories using scoring system."""
    question_lower = question.lower()

    categories = {
        'waste_bins': ['bin', 'waste', 'rubbish', 'garbage', 'collection', 'recycling', 'kerbside', 'composting', 'green waste', 'hard waste', 'bulky', 'e-waste', 'organics'],
        'rates': ['rates', 'council rates', 'rate', 'valuation', 'assessment', 'annual', 'payment', 'bill', 'invoice', 'property value', 'rate notice'],
        'planning': ['planning', 'development', 'permit', 'application', 'approval', 'zoning', 'building', 'construction', 'renovation', 'extension', 'subdivision', 'build', 'construct', 'deck', 'fence', 'pool', 'shed', 'carport', 'pergola', 'granny flat', 'demolish', 'retaining wall', 'setback', 'dwelling'],
        'roads': ['road', 'street', 'pothole', 'repair', 'maintenance', 'footpath', 'pavement', 'drainage', 'gravel', 'sealed road', 'street light', 'report', 'issue', 'graffiti', 'vandalism', 'damaged', 'broken', 'hazard', 'safety', 'sign'],
        'pets': ['pet', 'dog', 'cat', 'animal', 'registration', 'control', 'microchip', 'vaccination', 'de-sexing', 'pound', 'dangerous dog'],
        'property': ['property', 'land', 'title', 'boundary', 'land registration', 'property info', 'property details'],
        'family': ['family', 'child care', 'kindergarten', 'school', 'community', 'family support'],
        'community': ['community', 'event', 'service', 'local', 'volunteer', 'festival', 'hall hire', 'group', 'library', 'libraries', 'swimming', 'leisure', 'recreation', 'centre', 'center', 'hall', 'tourism', 'visitor', 'museum', 'arts', 'culture', 'sport', 'venue', 'hire'],
        'food_business': ['food', 'restaurant', 'cafe', 'business', 'registration', 'license', 'health permit', 'food safety'],
        'contact': ['contact', 'phone', 'email', 'office', 'department', 'hours', 'location', 'address', 'emergency', 'after hours', 'urgent', 'open', 'closed', 'when', 'where'],
        'environment': ['environment', 'sustainability', 'water', 'energy', 'green', 'climate', 'conservation'],
        'legal': ['law', 'regulation', 'bylaw', 'legislation', 'legal', 'rights', 'compliance'],
        'grants': ['grant', 'funding', 'subsidy', 'assistance', 'support', 'loan', 'rebate'],
        'local_laws': ['local laws', 'bylaws', 'ordinance', 'noise', 'parking', 'dogs', 'cats'],
        'forms': ['form', 'application', 'document', 'download', 'template'],
        'library': ['library', 'book', 'library card', 'librarian', 'reading', 'borrow', 'return', 'program'],
        'tourism': ['tourism', 'tourist', 'visitor', 'attraction', 'accommodation', 'tour', 'event', 'things to do'],
        'potential_api_abuse': ['hack', 'breach', 'attack', 'vulnerable', 'exploit', 'malware'],
        'off_topic': []
    }

    scores = {}
    for category, keywords in categories.items():
        score = 0
        for keyword in keywords:
            if keyword in question_lower:
                score += 1
        if score > 0:
            scores[category] = score

    if not scores:
        return 'off_topic'

    return max(scores, key=scores.get)


def hash_ip(ip_address):
    """Hash IP address for privacy."""
    return hashlib.sha256(ip_address.encode()).hexdigest()[:16]


def log_query_basic(ip_address, filtered_question, response_time, category):
    """Log basic query information to JSONL."""
    log_entry = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "hashed_ip": hash_ip(ip_address),
        "filtered_question": filtered_question,
        "response_time_ms": response_time,
        "category": category
    }

    log_file = Path(__file__).parent / "query_log_basic.jsonl"
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(log_entry) + '\n')


def log_query_full(ip_address, filtered_question, response_time, category, filtered_answer, answer_length, thumbs, sources, follow_up):
    """Log full query information to JSONL."""
    log_entry = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "hashed_ip": hash_ip(ip_address),
        "filtered_question": filtered_question,
        "response_time_ms": response_time,
        "category": category,
        "filtered_answer": filtered_answer,
        "answer_length": answer_length,
        "thumbs": thumbs,
        "sources": sources,
        "follow_up": follow_up,
        "prompt_version": PROMPT_VERSION,
        "kb_hash": knowledge_hash
    }

    log_file = Path(__file__).parent / "query_log_full.jsonl"
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(log_entry) + '\n')


def log_feedback_csv(ip_address, question, answer, feedback, timestamp):
    """Log feedback to CSV."""
    csv_file = Path(__file__).parent / "feedback.csv"

    file_exists = csv_file.exists()

    with open(csv_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['timestamp', 'hashed_ip', 'question', 'answer', 'feedback'])
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'timestamp': timestamp,
            'hashed_ip': hash_ip(ip_address),
            'question': question,
            'answer': answer,
            'feedback': feedback
        })


def build_system_prompt(messages, bin_context=""):
    """Build system prompt from knowledge base with current date and version."""
    today = datetime.date.today().strftime('%A %d %B %Y')
    prompt = knowledge_base.replace('__CURRENT_DATE__', today)
    prompt = prompt.replace('__PROMPT_VERSION__', PROMPT_VERSION)
    if bin_context:
        prompt += f"\n\n--- LIVE BIN DATA ---\n{bin_context}"
    return prompt


def handle_search_protocol(question):
    """Parse search: protocol from knowledge base."""
    if question.startswith("search:"):
        search_term = question[7:].strip()

        # Parse URL directory format
        matches = []
        for line in knowledge_base.split('\n'):
            if search_term.lower() in line.lower():
                matches.append(line)

        return matches[:10] if matches else ["No results found for search term."]

    return None


class CouncilGeniusHandler(BaseHTTPRequestHandler):
    """HTTP request handler for CouncilGenius V8."""

    def do_GET(self):
        """Handle GET requests."""
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path == '/':
            self.serve_page()
        elif path == '/health':
            self.serve_health()
        elif path == '/knowledge.txt':
            self.serve_knowledge()
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found\n")

    def do_POST(self):
        """Handle POST requests."""
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path == '/chat':
            self.handle_chat()
        elif path == '/feedback':
            self.handle_feedback()
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found\n")

    def do_OPTIONS(self):
        """Handle OPTIONS requests for CORS."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def serve_page(self):
        """Serve the main page."""
        page_path = Path(__file__).parent / "page.html"

        if page_path.exists():
            with open(page_path, 'r', encoding='utf-8') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(content.encode())
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Page not found\n")

    def serve_health(self):
        """Serve health check with V8 details."""
        uptime = time.time() - startup_time

        health = {
            "status": "healthy",
            "council": COUNCIL_NAME,
            "knowledge_loaded": knowledge_lines > 0,
            "knowledge_lines": knowledge_lines,
            "knowledge_hash": knowledge_hash,
            "bin_mode": BIN_LOOKUP_MODE,
            "model": MODEL,
            "prompt_version": PROMPT_VERSION,
            "uptime_seconds": int(uptime),
            "total_queries": self.count_queries()
        }

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(health).encode())

    def serve_knowledge(self):
        """Serve the knowledge base."""
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(knowledge_base.encode())

    def handle_chat(self):
        """Handle chat POST requests."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            data = json.loads(body)
            messages = data.get('messages', [])
            user_message = messages[-1]['content'] if messages else ''

            if not user_message:
                self.send_error_response(400, "Question required")
                return

            # Get client IP
            client_ip = self.client_address[0]

            # Check for search protocol
            search_results = handle_search_protocol(user_message)
            if search_results is not None:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                resp = {
                    "response": "\n".join(search_results),
                    "category": "search"
                }
                self.wfile.write(json.dumps(resp).encode())
                return

            # Filter PII
            filtered_question = filter_pii(user_message)

            # Classify category
            category = classify(user_message)

            # Check for abuse or off-topic
            if category == 'potential_api_abuse':
                self.send_json_response({
                    "response": f"I'm the {COUNCIL_NAME} Community Assistant. I can help with council services like bins, rates, planning, and pets. What can I help you with?",
                    "category": "potential_api_abuse"
                })
                log_query_basic(client_ip, filtered_question, 0, category)
                return


            # Build system prompt with bin context
            system_prompt = build_system_prompt(messages)

            # Call Anthropic API via urllib
            start_time = time.time()
            try:
                api_body = json.dumps({
                    'model': MODEL,
                    'max_tokens': MAX_TOKENS,
                    'system': system_prompt,
                    'messages': [{'role': m['role'], 'content': m['content']} for m in messages if isinstance(m, dict)]
                }).encode()

                req = urllib.request.Request(
                    'https://api.anthropic.com/v1/messages',
                    data=api_body,
                    headers={
                        'Content-Type': 'application/json',
                        'x-api-key': ANTHROPIC_API_KEY,
                        'anthropic-version': '2023-06-01'
                    }
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read())

                answer = result['content'][0]['text']
                response_time = int((time.time() - start_time) * 1000)

            except Exception as api_error:
                logger.error(f"API error: {str(api_error)}")
                self.send_json_response({
                    "response": f"Sorry, I couldn't process your question right now. Please try again, or call {COUNCIL_NAME} on {COUNCIL_PHONE} for help.",
                    "error": True,
                    "category": category
                })
                return

            # Filter answer
            filtered_answer = filter_pii(answer)

            # Log queries
            log_query_basic(client_ip, filtered_question, response_time, category)
            log_query_full(
                client_ip,
                filtered_question,
                response_time,
                category,
                filtered_answer,
                len(answer),
                None,
                [],
                False
            )

            # Send response
            self.send_json_response({
                "response": answer,
                "category": category,
                "bin_info": None
            })

        except json.JSONDecodeError:
            self.send_error_response(400, "Invalid JSON")
        except Exception as e:
            logger.error(f"Error in handle_chat: {str(e)}")
            self.send_error_response(500, "Internal server error")

    def handle_feedback(self):
        """Handle feedback POST requests."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            data = json.loads(body)
            question = data.get('question', '')
            answer = data.get('answer', '')
            feedback = data.get('feedback', '')

            client_ip = self.client_address[0]
            timestamp = datetime.datetime.utcnow().isoformat()

            log_feedback_csv(client_ip, question, answer, feedback, timestamp)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode())

        except Exception as e:
            logger.error(f"Error in handle_feedback: {str(e)}")
            self.send_error_response(500, "Internal server error")

    def send_json_response(self, data, status=200):
        """Send JSON response with CORS headers."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_error_response(self, code, message):
        """Send error response."""
        self.send_json_response({"error": message}, code)

    def count_queries(self):
        """Count total queries from logs."""
        log_file = Path(__file__).parent / "query_log_basic.jsonl"
        if log_file.exists():
            with open(log_file, 'r', encoding='utf-8') as f:
                return sum(1 for _ in f)
        return 0

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def main():
    """Start the server."""
    load_knowledge_base()

    print(f"\n{'='*60}")
    print(f"CouncilGenius V8 - {COUNCIL_NAME}")
    print(f"{'='*60}")
    print(f"Model: {MODEL}")
    print(f"Knowledge Lines: {knowledge_lines}")
    print(f"Knowledge Hash: {knowledge_hash}")
    print(f"Prompt Version: {PROMPT_VERSION}")
    print(f"Bin Mode: {BIN_LOOKUP_MODE}")
    print(f"Port: {PORT}")
    print(f"{'='*60}\n")

    server_address = ('', PORT)
    httpd = HTTPServer(server_address, CouncilGeniusHandler)

    logger.info(f"Starting server on port {PORT}")
    print(f"Server running at http://localhost:{PORT}/")
    print(f"Press Ctrl+C to stop\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        print("\nServer stopped.")
        sys.exit(0)


if __name__ == '__main__':
    main()
