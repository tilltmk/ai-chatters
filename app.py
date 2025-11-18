from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import sqlite3
import json
import requests
import secrets
import os
from functools import wraps
import anthropic
import openai
from translations import get_translation, get_all_translations

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['DATABASE'] = 'ai_chatters.db'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def get_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                ui_language TEXT DEFAULT 'de',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            );
            
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                api_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, service)
            );
            
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                topic TEXT,
                participants TEXT NOT NULL,
                auto_mode BOOLEAN DEFAULT 0,
                models_aware BOOLEAN DEFAULT 1,
                conversation_language TEXT DEFAULT 'auto',
                max_turns INTEGER DEFAULT 20,
                current_turn INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
            
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            );

            CREATE TABLE IF NOT EXISTS conversation_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                note_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                model_generated TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            );

            CREATE TABLE IF NOT EXISTS benchmark_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                models TEXT NOT NULL,
                total_questions INTEGER DEFAULT 0,
                completed_questions INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );

            CREATE TABLE IF NOT EXISTS benchmark_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                question TEXT NOT NULL,
                difficulty TEXT DEFAULT 'medium',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS benchmark_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                response TEXT NOT NULL,
                response_time REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES benchmark_sessions (id),
                FOREIGN KEY (question_id) REFERENCES benchmark_questions (id)
            );

            CREATE TABLE IF NOT EXISTS benchmark_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                selected_response_id INTEGER NOT NULL,
                rating_value INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES benchmark_sessions (id),
                FOREIGN KEY (question_id) REFERENCES benchmark_questions (id),
                FOREIGN KEY (selected_response_id) REFERENCES benchmark_responses (id)
            );
        ''')
        db.commit()

class User(UserMixin):
    def __init__(self, id, username, email, ui_language='de'):
        self.id = id
        self.username = username
        self.email = email
        self.ui_language = ui_language

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        ui_lang = user['ui_language'] if 'ui_language' in user.keys() else 'de'
        return User(user['id'], user['username'], user['email'], ui_lang)
    return None

def get_user_language():
    """Get the current user's UI language preference."""
    if current_user.is_authenticated:
        return current_user.ui_language
    return session.get('ui_language', 'de')

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    lang = session.get('ui_language', 'de')
    return render_template('index.html', t=get_all_translations(lang), lang=lang)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        
        db = get_db()
        existing = db.execute('SELECT id FROM users WHERE username = ? OR email = ?', 
                              (username, email)).fetchone()
        
        if existing:
            return jsonify({'error': 'Username or email already exists'}), 400
        
        password_hash = generate_password_hash(password)
        db.execute('INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                  (username, email, password_hash))
        db.commit()
        
        return jsonify({'success': True}), 201
    
    lang = session.get('ui_language', 'de')
    return render_template('register.html', t=get_all_translations(lang), lang=lang)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ? OR email = ?', 
                         (username, username)).fetchone()
        
        if user and check_password_hash(user['password_hash'], password):
            user_obj = User(user['id'], user['username'], user['email'])
            login_user(user_obj, remember=True)
            return jsonify({'success': True}), 200
        
        return jsonify({'error': 'Invalid credentials'}), 401
    
    lang = session.get('ui_language', 'de')
    return render_template('login.html', t=get_all_translations(lang), lang=lang)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    conversations = db.execute('''
        SELECT * FROM conversations 
        WHERE user_id = ? 
        ORDER BY updated_at DESC
    ''', (current_user.id,)).fetchall()
    
    # Parse participants JSON for each conversation
    parsed_conversations = []
    for conv in conversations:
        conv_dict = dict(conv)
        try:
            conv_dict['participants_list'] = json.loads(conv_dict['participants'])
        except:
            conv_dict['participants_list'] = []
        parsed_conversations.append(conv_dict)
    
    api_keys = db.execute('''
        SELECT service FROM api_keys 
        WHERE user_id = ?
    ''', (current_user.id,)).fetchall()
    
    configured_services = [key['service'] for key in api_keys]
    
    lang = get_user_language()
    return render_template('dashboard.html', 
                         conversations=parsed_conversations,
                         configured_services=configured_services,
                         t=get_all_translations(lang),
                         lang=lang)

@app.route('/settings')
@login_required
def settings():
    db = get_db()
    api_keys = db.execute('''
        SELECT service, 
               CASE 
                   WHEN api_key IS NOT NULL THEN 1 
                   ELSE 0 
               END as configured
        FROM api_keys 
        WHERE user_id = ?
    ''', (current_user.id,)).fetchall()
    
    services = {'ollama': False, 'openai': False, 'anthropic': False, 'google': False}
    for key in api_keys:
        services[key['service']] = bool(key['configured'])
    
    lang = get_user_language()
    return render_template('settings.html', 
                         services=services,
                         t=get_all_translations(lang),
                         lang=lang,
                         current_language=lang)

@app.route('/api/change-language', methods=['POST'])
def change_language():
    data = request.get_json()
    lang = data.get('language', 'de')
    
    if lang not in ['de', 'en']:
        lang = 'de'
    
    session['ui_language'] = lang
    
    if current_user.is_authenticated:
        db = get_db()
        db.execute('UPDATE users SET ui_language = ? WHERE id = ?', 
                  (lang, current_user.id))
        db.commit()
        current_user.ui_language = lang
    
    return jsonify({'success': True}), 200

@app.route('/api/save-api-key', methods=['POST'])
@login_required
def save_api_key():
    data = request.get_json()
    service = data.get('service')
    api_key = data.get('api_key')
    
    if service not in ['ollama', 'openai', 'anthropic', 'google']:
        return jsonify({'error': 'Invalid service'}), 400
    
    db = get_db()
    db.execute('''
        INSERT OR REPLACE INTO api_keys (user_id, service, api_key)
        VALUES (?, ?, ?)
    ''', (current_user.id, service, api_key))
    db.commit()
    
    return jsonify({'success': True}), 200

@app.route('/api/test-connection', methods=['POST'])
@login_required
def test_connection():
    data = request.get_json()
    service = data.get('service')
    
    db = get_db()
    api_key_row = db.execute('''
        SELECT api_key FROM api_keys 
        WHERE user_id = ? AND service = ?
    ''', (current_user.id, service)).fetchone()
    
    if not api_key_row:
        return jsonify({'error': 'API key not configured'}), 400
    
    api_key = api_key_row['api_key']
    
    try:
        if service == 'ollama':
            response = requests.get('http://localhost:11434/api/tags')
            if response.status_code == 200:
                return jsonify({'success': True, 'models': response.json().get('models', [])}), 200
        
        elif service == 'openai':
            client = openai.OpenAI(api_key=api_key)
            models = client.models.list()
            return jsonify({'success': True, 'models': [m.id for m in models.data]}), 200
        
        elif service == 'anthropic':
            # Test Anthropic API by fetching models
            try:
                headers = {
                    'x-api-key': api_key,
                    'anthropic-version': '2023-06-01'
                }
                response = requests.get('https://api.anthropic.com/v1/models', headers=headers)
                if response.status_code == 200:
                    models_data = response.json().get('data', [])
                    model_ids = [model['id'] for model in models_data]
                    return jsonify({'success': True, 'models': model_ids}), 200
                else:
                    return jsonify({'error': 'Could not fetch models'}), 400
            except Exception as e:
                return jsonify({'error': f'API test failed: {str(e)}'}), 400

        elif service == 'google':
            try:
                # Simple test without listing models to avoid potential API issues
                import google.generativeai as genai
                genai.configure(api_key=api_key)

                # Test with a simple model instantiation
                model = genai.GenerativeModel('gemini-pro')

                # Return success with fallback models
                fallback_models = [
                    'gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-pro', 'gemini-pro-vision'
                ]
                return jsonify({'success': True, 'models': fallback_models}), 200
            except ImportError as e:
                return jsonify({'error': f'Google Generative AI library not installed: {str(e)}'}), 400
            except Exception as e:
                return jsonify({'error': f'Google API test failed: {str(e)}'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Connection failed'}), 500

@app.route('/conversation/<int:conversation_id>')
@login_required
def conversation(conversation_id):
    db = get_db()
    conv = db.execute('''
        SELECT * FROM conversations 
        WHERE id = ? AND user_id = ?
    ''', (conversation_id, current_user.id)).fetchone()
    
    if not conv:
        return redirect(url_for('dashboard'))
    
    messages = db.execute('''
        SELECT * FROM messages 
        WHERE conversation_id = ? 
        ORDER BY created_at ASC
    ''', (conversation_id,)).fetchall()
    
    lang = get_user_language()
    return render_template('conversation.html', 
                         conversation=conv, 
                         messages=messages,
                         t=get_all_translations(lang),
                         lang=lang)

@app.route('/api/conversation/new', methods=['POST'])
@login_required
def new_conversation():
    data = request.get_json()
    if data is None:
        return jsonify({'error': 'Invalid JSON in request'}), 400

    title = data.get('title', 'New Conversation')
    topic = data.get('topic', '')
    participants = json.dumps(data.get('participants', []))
    auto_mode = data.get('auto_mode', False)
    models_aware = data.get('models_aware', True)
    conversation_language = data.get('conversation_language', 'auto')
    max_turns = data.get('max_turns', 20)
    
    db = get_db()
    cursor = db.execute('''
        INSERT INTO conversations (user_id, title, topic, participants, auto_mode, models_aware, conversation_language, max_turns)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (current_user.id, title, topic, participants, auto_mode, models_aware, conversation_language, max_turns))
    db.commit()
    
    conv_id = cursor.lastrowid
    
    if auto_mode and topic:
        # Start automatic conversation with the topic
        participants_list = json.loads(participants)
        if participants_list:
            first_model = participants_list[0]
            db.execute('''
                INSERT INTO messages (conversation_id, model, role, content)
                VALUES (?, ?, ?, ?)
            ''', (conv_id, 'System', 'system', f"Topic: {topic}"))
            db.commit()
    
    return jsonify({'id': conv_id}), 201

@app.route('/api/conversation/<int:conversation_id>/auto-continue', methods=['POST'])
@login_required
def auto_continue_conversation(conversation_id):
    db = get_db()
    
    conv = db.execute('''
        SELECT * FROM conversations 
        WHERE id = ? AND user_id = ?
    ''', (conversation_id, current_user.id)).fetchone()
    
    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404
    
    participants = json.loads(conv['participants'])
    current_turn = conv['current_turn'] or 0
    max_turns = conv['max_turns'] or 20
    
    if current_turn >= max_turns:
        return jsonify({'error': 'Max turns reached'}), 400
    
    # Get last message
    last_msg = db.execute('''
        SELECT model, content FROM messages 
        WHERE conversation_id = ? 
        ORDER BY created_at DESC 
        LIMIT 1
    ''', (conversation_id,)).fetchone()
    
    if not last_msg:
        # Start with topic if available
        if conv['topic']:
            first_model = participants[0] if participants else None
            if first_model:
                response = get_ai_response(first_model, conv['topic'], conversation_id)
                db.execute('''
                    INSERT INTO messages (conversation_id, model, role, content)
                    VALUES (?, ?, ?, ?)
                ''', (conversation_id, first_model, 'assistant', response))
                db.execute('''
                    UPDATE conversations 
                    SET current_turn = current_turn + 1, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                ''', (conversation_id,))
                db.commit()
                return jsonify({'model': first_model, 'content': response, 'turn': current_turn + 1}), 200
    else:
        # Determine next speaker
        last_model = last_msg['model']
        if last_model in participants:
            current_idx = participants.index(last_model)
            next_idx = (current_idx + 1) % len(participants)
            next_model = participants[next_idx]
        else:
            next_model = participants[0] if participants else None
        
        if next_model:
            response = get_ai_response(next_model, last_msg['content'], conversation_id)
            db.execute('''
                INSERT INTO messages (conversation_id, model, role, content)
                VALUES (?, ?, ?, ?)
            ''', (conversation_id, next_model, 'assistant', response))
            db.execute('''
                UPDATE conversations 
                SET current_turn = current_turn + 1, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (conversation_id,))
            db.commit()
            return jsonify({'model': next_model, 'content': response, 'turn': current_turn + 1}), 200
    
    return jsonify({'error': 'Could not continue conversation'}), 400

@app.route('/api/conversation/<int:conversation_id>/message', methods=['POST'])
@login_required
def send_message(conversation_id):
    data = request.get_json()
    model = data.get('model')
    content = data.get('content')
    
    db = get_db()
    
    conv = db.execute('''
        SELECT * FROM conversations 
        WHERE id = ? AND user_id = ?
    ''', (conversation_id, current_user.id)).fetchone()
    
    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404
    
    participants = json.loads(conv['participants'])
    
    db.execute('''
        INSERT INTO messages (conversation_id, model, role, content)
        VALUES (?, ?, ?, ?)
    ''', (conversation_id, model, 'user', content))
    
    db.execute('''
        UPDATE conversations 
        SET updated_at = CURRENT_TIMESTAMP 
        WHERE id = ?
    ''', (conversation_id,))
    db.commit()
    
    responses = []
    
    for participant in participants:
        if participant == model:
            continue
        
        response = get_ai_response(participant, content, conversation_id)
        if response:
            db.execute('''
                INSERT INTO messages (conversation_id, model, role, content)
                VALUES (?, ?, ?, ?)
            ''', (conversation_id, participant, 'assistant', response))
            db.commit()
            responses.append({'model': participant, 'content': response})
    
    return jsonify({'responses': responses}), 200

def get_ai_response(model_service, prompt, conversation_id):
    db = get_db()
    
    service = model_service.split(':')[0]
    model_name = model_service.split(':')[1] if ':' in model_service else None
    
    api_key_row = db.execute('''
        SELECT api_key FROM api_keys 
        WHERE user_id = ? AND service = ?
    ''', (current_user.id, service)).fetchone()
    
    if not api_key_row:
        return f"Error: {service} API key not configured"
    
    api_key = api_key_row['api_key']
    
    # Get conversation settings
    conv = db.execute('''
        SELECT models_aware, topic, participants, conversation_language FROM conversations 
        WHERE id = ?
    ''', (conversation_id,)).fetchone()
    
    recent_messages = db.execute('''
        SELECT model, role, content FROM messages 
        WHERE conversation_id = ? 
        ORDER BY created_at DESC 
        LIMIT 10
    ''', (conversation_id,)).fetchall()
    
    context = []
    
    # Determine conversation language
    conv_lang = 'en'  # Default
    if conv and 'conversation_language' in conv.keys():
        if conv['conversation_language'] == 'de':
            conv_lang = 'de'
        elif conv['conversation_language'] == 'en':
            conv_lang = 'en'
        elif conv['conversation_language'] == 'auto':
            # Auto-detect from topic or use user's UI language
            conv_lang = get_user_language()
    
    # Add awareness context if enabled
    if conv and conv['models_aware']:
        participants = json.loads(conv['participants'])
        
        if conv_lang == 'de':
            awareness_msg = f"Du bist {model_service} in einer Konversation mit diesen KI-Modellen: {', '.join(participants)}. "
            if conv['topic']:
                awareness_msg += f"Das Thema ist: {conv['topic']}. "
            awareness_msg += "Bitte antworte natürlich als Teil dieser Multi-KI-Konversation auf Deutsch."
        else:
            awareness_msg = f"You are {model_service} in a conversation with these AI models: {', '.join(participants)}. "
            if conv['topic']:
                awareness_msg += f"The topic is: {conv['topic']}. "
            awareness_msg += "Please respond naturally as part of this multi-AI conversation in English."
        
        context.append({'role': 'system', 'content': awareness_msg})
    elif conv and conv['topic']:
        if conv_lang == 'de':
            context.append({'role': 'system', 'content': f"Thema: {conv['topic']}. Bitte antworte auf Deutsch."})
        else:
            context.append({'role': 'system', 'content': f"Topic: {conv['topic']}. Please respond in English."})
    
    for msg in reversed(recent_messages):
        if msg['role'] == 'system':
            continue
        role = 'assistant' if msg['role'] == 'assistant' else 'user'
        content = msg['content']
        if conv and conv['models_aware'] and msg['model'] != model_service:
            content = f"[{msg['model']}]: {content}"
        context.append({'role': role, 'content': content})
    
    try:
        if service == 'ollama':
            if not model_name:
                return "Error: No model specified for Ollama"
            response = requests.post('http://localhost:11434/api/chat', json={
                'model': model_name,
                'messages': context + [{'role': 'user', 'content': prompt}],
                'stream': False
            })
            if response.status_code == 200:
                return response.json()['message']['content']
        
        elif service == 'openai':
            if not model_name:
                return "Error: No model specified for OpenAI"
            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=model_name,
                messages=context + [{'role': 'user', 'content': prompt}]
            )
            return response.choices[0].message.content
        
        elif service == 'anthropic':
            client = anthropic.Client(api_key=api_key)
            # Use the newer API format
            system_msg = None
            user_messages = []
            
            for msg in context:
                if msg['role'] == 'system':
                    system_msg = msg['content']
                else:
                    user_messages.append(msg)
            
            user_messages.append({'role': 'user', 'content': prompt})
            
            # Use the model name directly as provided
            if not model_name:
                return "Error: No model specified for Anthropic"
            actual_model = model_name
            
            response = client.messages.create(
                model=actual_model,
                max_tokens=1000,
                system=system_msg if system_msg else None,
                messages=user_messages
            )
            return response.content[0].text

        elif service == 'google':
            if not model_name:
                return "Error: No model specified for Google"

            try:
                import google.generativeai as genai
                genai.configure(api_key=api_key)

                # Use a more straightforward approach for Google API
                model = genai.GenerativeModel(model_name)

                # Combine context and prompt into a simple text
                full_prompt = ""

                # Add system message as context if present
                for msg in context:
                    if msg['role'] == 'system':
                        full_prompt += f"Context: {msg['content']}\n\n"

                # Add conversation history
                for msg in context:
                    if msg['role'] == 'user':
                        full_prompt += f"User: {msg['content']}\n"
                    elif msg['role'] == 'assistant':
                        full_prompt += f"Assistant: {msg['content']}\n"

                # Add current prompt
                full_prompt += f"User: {prompt}\nAssistant:"

                # Generate response
                response = model.generate_content(full_prompt)
                return response.text

            except ImportError as e:
                return f"Error: Google Generative AI library not installed - {str(e)}"
            except Exception as e:
                return f"Error: Google API failed - {str(e)}"

    except Exception as e:
        return f"Error: {str(e)}"

    return "Error: Unable to get response"

@app.route('/api/conversation/<int:conversation_id>/export', methods=['GET'])
@login_required
def export_conversation(conversation_id):
    format_type = request.args.get('format', 'json')
    include_metadata = request.args.get('metadata', 'true').lower() == 'true'

    db = get_db()

    # Verify conversation ownership
    conv = db.execute('''
        SELECT * FROM conversations
        WHERE id = ? AND user_id = ?
    ''', (conversation_id, current_user.id)).fetchone()

    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404

    # Get all messages
    messages = db.execute('''
        SELECT model, role, content, created_at FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC
    ''', (conversation_id,)).fetchall()

    # Prepare export data
    export_data = {
        'conversation': {
            'id': conv['id'],
            'title': conv['title'],
            'topic': conv['topic'],
            'participants': json.loads(conv['participants']),
            'created_at': conv['created_at'],
            'updated_at': conv['updated_at']
        } if include_metadata else {},
        'messages': []
    }

    for msg in messages:
        message_data = {
            'model': msg['model'],
            'role': msg['role'],
            'content': msg['content'],
            'timestamp': msg['created_at']
        }
        export_data['messages'].append(message_data)

    if format_type == 'txt':
        # Plain text format
        output = ""
        if include_metadata:
            output += f"Conversation: {conv['title']}\n"
            if conv['topic']:
                output += f"Topic: {conv['topic']}\n"
            output += f"Participants: {', '.join(json.loads(conv['participants']))}\n"
            output += f"Created: {conv['created_at']}\n\n"
            output += "=" * 50 + "\n\n"

        for msg in messages:
            output += f"[{msg['created_at']}] {msg['model']}: {msg['content']}\n\n"

        return output, 200, {'Content-Type': 'text/plain'}

    elif format_type == 'md':
        # Markdown format
        output = ""
        if include_metadata:
            output += f"# {conv['title']}\n\n"
            if conv['topic']:
                output += f"**Topic:** {conv['topic']}\n\n"
            output += f"**Participants:** {', '.join(json.loads(conv['participants']))}\n\n"
            output += f"**Created:** {conv['created_at']}\n\n"
            output += "---\n\n"

        for msg in messages:
            output += f"## {msg['model']}\n"
            output += f"*{msg['created_at']}*\n\n"
            output += f"{msg['content']}\n\n"

        return output, 200, {'Content-Type': 'text/markdown'}

    else:
        # JSON format (default)
        return jsonify(export_data), 200

@app.route('/api/conversation/<int:conversation_id>/analyze-context', methods=['GET'])
@login_required
def analyze_context_compromise(conversation_id):
    db = get_db()

    # Verify conversation ownership
    conv = db.execute('''
        SELECT * FROM conversations
        WHERE id = ? AND user_id = ?
    ''', (conversation_id, current_user.id)).fetchone()

    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404

    # Get all messages with character counts
    messages = db.execute('''
        SELECT model, role, content, created_at FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC
    ''', (conversation_id,)).fetchall()

    total_chars = 0
    total_tokens_estimate = 0
    message_count = len(messages)
    model_participation = {}
    context_timeline = []

    for i, msg in enumerate(messages):
        chars = len(msg['content'])
        tokens_estimate = chars // 4  # Rough estimate
        total_chars += chars
        total_tokens_estimate += tokens_estimate

        # Track model participation
        model = msg['model']
        if model not in model_participation:
            model_participation[model] = {'messages': 0, 'chars': 0, 'tokens_estimate': 0}
        model_participation[model]['messages'] += 1
        model_participation[model]['chars'] += chars
        model_participation[model]['tokens_estimate'] += tokens_estimate

        # Context timeline entry
        context_timeline.append({
            'message_index': i + 1,
            'timestamp': msg['created_at'],
            'model': model,
            'chars': chars,
            'tokens_estimate': tokens_estimate,
            'cumulative_chars': total_chars,
            'cumulative_tokens': total_tokens_estimate
        })

    # Context compromise analysis
    analysis = {
        'summary': {
            'total_messages': message_count,
            'total_characters': total_chars,
            'estimated_tokens': total_tokens_estimate,
            'participants': list(model_participation.keys())
        },
        'context_health': {
            'status': 'healthy' if total_tokens_estimate < 8000 else 'degraded' if total_tokens_estimate < 30000 else 'compromised',
            'token_usage_percentage': min(100, (total_tokens_estimate / 32000) * 100),
            'estimated_context_window_remaining': max(0, 32000 - total_tokens_estimate)
        },
        'model_participation': model_participation,
        'timeline': context_timeline,
        'recommendations': []
    }

    # Generate recommendations
    if total_tokens_estimate > 30000:
        analysis['recommendations'].append({
            'type': 'critical',
            'message': 'Context window severely compromised. Models may lose track of early conversation.'
        })
    elif total_tokens_estimate > 16000:
        analysis['recommendations'].append({
            'type': 'warning',
            'message': 'Context window getting large. Consider summarizing or starting new conversation.'
        })

    if len(model_participation) > 4:
        analysis['recommendations'].append({
            'type': 'info',
            'message': 'Many participants may lead to confusion and reduced response quality.'
        })

    return jsonify(analysis), 200

@app.route('/api/conversation/<int:conversation_id>/generate-notes', methods=['POST'])
@login_required
def generate_automatic_notes(conversation_id):
    data = request.get_json()
    note_model = data.get('model', 'anthropic:claude-3-haiku-20240307')

    db = get_db()

    # Verify conversation ownership
    conv = db.execute('''
        SELECT * FROM conversations
        WHERE id = ? AND user_id = ?
    ''', (conversation_id, current_user.id)).fetchone()

    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404

    # Get recent messages for analysis
    messages = db.execute('''
        SELECT model, role, content, created_at FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at DESC
        LIMIT 50
    ''', (conversation_id,)).fetchall()

    if not messages:
        return jsonify({'error': 'No messages to analyze'}), 400

    # Prepare context for note generation
    conversation_text = f"Conversation Topic: {conv['topic']}\n\n"
    for msg in reversed(messages):
        conversation_text += f"[{msg['model']}]: {msg['content']}\n\n"

    # Generate different types of notes
    notes_generated = []

    # 1. Summary Note
    summary_prompt = f"""Please create a concise summary of this AI conversation:

{conversation_text}

Focus on:
- Key topics discussed
- Main conclusions or decisions
- Important insights or breakthroughs
- Unresolved questions

Provide a structured summary in German."""

    summary = get_ai_response(note_model, summary_prompt, conversation_id)
    if summary and not summary.startswith("Error:"):
        db.execute('''
            INSERT INTO conversation_notes (conversation_id, note_type, title, content, model_generated)
            VALUES (?, ?, ?, ?, ?)
        ''', (conversation_id, 'summary', 'Gespräch Zusammenfassung', summary, note_model))
        notes_generated.append({'type': 'summary', 'title': 'Gespräch Zusammenfassung'})

    # 2. Key Insights Note
    insights_prompt = f"""Analyze this AI conversation and extract key insights, patterns, and interesting observations:

{conversation_text}

Look for:
- Novel ideas or creative solutions
- Interesting interaction patterns between AI models
- Contradictions or disagreements
- Emerging themes or concepts
- Technical details or methodologies

Present your findings as structured insights in German."""

    insights = get_ai_response(note_model, insights_prompt, conversation_id)
    if insights and not insights.startswith("Error:"):
        db.execute('''
            INSERT INTO conversation_notes (conversation_id, note_type, title, content, model_generated)
            VALUES (?, ?, ?, ?, ?)
        ''', (conversation_id, 'insights', 'Wichtige Erkenntnisse', insights, note_model))
        notes_generated.append({'type': 'insights', 'title': 'Wichtige Erkenntnisse'})

    # 3. Action Items Note
    action_prompt = f"""Extract actionable items, recommendations, and next steps from this AI conversation:

{conversation_text}

Identify:
- Specific action items mentioned
- Recommendations made by any participant
- Follow-up questions that should be explored
- Resources or tools mentioned
- Implementation suggestions

Format as a clear action list in German."""

    actions = get_ai_response(note_model, action_prompt, conversation_id)
    if actions and not actions.startswith("Error:"):
        db.execute('''
            INSERT INTO conversation_notes (conversation_id, note_type, title, content, model_generated)
            VALUES (?, ?, ?, ?, ?)
        ''', (conversation_id, 'actions', 'Handlungsempfehlungen', actions, note_model))
        notes_generated.append({'type': 'actions', 'title': 'Handlungsempfehlungen'})

    # 4. Context Analysis Note
    context_analysis_prompt = f"""Analyze the context and conversation dynamics of this multi-AI discussion:

{conversation_text}

Evaluate:
- How well each AI model contributed to the discussion
- Whether models stayed on topic
- Quality of interactions between models
- Context retention throughout the conversation
- Overall conversation coherence and flow

Provide analysis in German."""

    context_analysis = get_ai_response(note_model, context_analysis_prompt, conversation_id)
    if context_analysis and not context_analysis.startswith("Error:"):
        db.execute('''
            INSERT INTO conversation_notes (conversation_id, note_type, title, content, model_generated)
            VALUES (?, ?, ?, ?, ?)
        ''', (conversation_id, 'context_analysis', 'Kontext Analyse', context_analysis, note_model))
        notes_generated.append({'type': 'context_analysis', 'title': 'Kontext Analyse'})

    db.commit()

    return jsonify({
        'success': True,
        'notes_generated': notes_generated,
        'total_notes': len(notes_generated)
    }), 200

@app.route('/api/conversation/<int:conversation_id>/notes', methods=['GET'])
@login_required
def get_conversation_notes(conversation_id):
    db = get_db()

    # Verify conversation ownership
    conv = db.execute('''
        SELECT id FROM conversations
        WHERE id = ? AND user_id = ?
    ''', (conversation_id, current_user.id)).fetchone()

    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404

    notes = db.execute('''
        SELECT * FROM conversation_notes
        WHERE conversation_id = ?
        ORDER BY created_at DESC
    ''', (conversation_id,)).fetchall()

    notes_list = []
    for note in notes:
        notes_list.append({
            'id': note['id'],
            'type': note['note_type'],
            'title': note['title'],
            'content': note['content'],
            'model_generated': note['model_generated'],
            'created_at': note['created_at']
        })

    return jsonify(notes_list), 200

@app.route('/api/models', methods=['GET'])
@login_required
def get_available_models():
    db = get_db()
    api_keys = db.execute('''
        SELECT service, api_key FROM api_keys 
        WHERE user_id = ?
    ''', (current_user.id,)).fetchall()
    
    available_models = []
    
    for key in api_keys:
        if key['service'] == 'ollama':
            try:
                response = requests.get('http://localhost:11434/api/tags')
                if response.status_code == 200:
                    models = response.json().get('models', [])
                    for model in models:
                        available_models.append({
                            'id': f"ollama:{model['name']}",
                            'name': f"Ollama - {model['name']}",
                            'service': 'ollama'
                        })
            except:
                pass
        
        elif key['service'] == 'openai':
            try:
                client = openai.OpenAI(api_key=key['api_key'])
                # Fetch all available models from OpenAI
                models_response = client.models.list()
                # Filter for chat models
                chat_prefixes = ['gpt-4', 'gpt-3.5', 'gpt-4o', 'o1', 'chatgpt']
                for model in models_response.data:
                    if any(model.id.startswith(prefix) for prefix in chat_prefixes):
                        # Clean up the name for display
                        display_name = model.id
                        if 'gpt-4o' in model.id:
                            display_name = 'GPT-4o' + model.id.replace('gpt-4o', '')
                        elif 'gpt-4' in model.id:
                            display_name = 'GPT-4' + model.id.replace('gpt-4', '')
                        elif 'gpt-3.5' in model.id:
                            display_name = 'GPT-3.5' + model.id.replace('gpt-3.5', '')
                        elif 'o1' in model.id:
                            display_name = 'O1' + model.id.replace('o1', '')

                        available_models.append({
                            'id': f'openai:{model.id}',
                            'name': f'OpenAI - {display_name}',
                            'service': 'openai'
                        })
            except Exception as e:
                # If error, add some fallback models to make testing possible
                fallback_models = [
                    'gpt-4o-mini', 'gpt-4o', 'gpt-4', 'gpt-3.5-turbo'
                ]
                for model_id in fallback_models:
                    available_models.append({
                        'id': f'openai:{model_id}',
                        'name': f'OpenAI - {model_id}',
                        'service': 'openai'
                    })
        
        elif key['service'] == 'anthropic':
            # Add known Anthropic models as fallback
            try:
                headers = {
                    'x-api-key': key['api_key'],
                    'anthropic-version': '2023-06-01'
                }
                response = requests.get('https://api.anthropic.com/v1/models', headers=headers)
                if response.status_code == 200:
                    models_data = response.json().get('data', [])
                    for model in models_data:
                        available_models.append({
                            'id': f"anthropic:{model['id']}",
                            'name': f"Anthropic - {model.get('display_name', model['id'])}",
                            'service': 'anthropic'
                        })
                else:
                    # Add fallback models
                    fallback_models = [
                        'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022',
                        'claude-3-opus-20240229', 'claude-3-sonnet-20240229', 'claude-3-haiku-20240307'
                    ]
                    for model_id in fallback_models:
                        available_models.append({
                            'id': f"anthropic:{model_id}",
                            'name': f"Anthropic - {model_id}",
                            'service': 'anthropic'
                        })
            except:
                # Add fallback models if error
                fallback_models = [
                    'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022',
                    'claude-3-opus-20240229', 'claude-3-sonnet-20240229', 'claude-3-haiku-20240307'
                ]
                for model_id in fallback_models:
                    available_models.append({
                        'id': f"anthropic:{model_id}",
                        'name': f"Anthropic - {model_id}",
                        'service': 'anthropic'
                    })

        elif key['service'] == 'google':
            # Always use fallback models for Google to avoid API issues during model listing
            fallback_models = [
                'gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-pro', 'gemini-pro-vision'
            ]
            for model_id in fallback_models:
                available_models.append({
                    'id': f"google:{model_id}",
                    'name': f"Google - {model_id}",
                    'service': 'google'
                })

    return jsonify(available_models)

# Benchmarking System Routes
@app.route('/benchmark')
@login_required
def benchmark():
    """Main benchmarking page."""
    lang = get_user_language()
    return render_template('benchmark.html',
                         t=get_all_translations(lang),
                         lang=lang)

@app.route('/api/benchmark/init-questions', methods=['POST'])
@login_required
def init_benchmark_questions():
    """Initialize the question pool for benchmarking."""
    db = get_db()

    # Check if questions already exist
    existing = db.execute('SELECT COUNT(*) as count FROM benchmark_questions').fetchone()
    if existing['count'] > 0:
        return jsonify({'success': True, 'message': 'Questions already initialized'}), 200

    # Question categories with diverse questions
    questions = [
        # Kreativität & Storytelling
        {'category': 'Kreativität', 'question': 'Schreibe eine kurze Geschichte über einen Roboter, der zum ersten Mal Musik hört.', 'difficulty': 'medium'},
        {'category': 'Kreativität', 'question': 'Erfinde ein neues Wort und erkläre seine Bedeutung mit einem praktischen Beispiel.', 'difficulty': 'easy'},
        {'category': 'Kreativität', 'question': 'Beschreibe einen Sonnenuntergang aus der Perspektive eines Malers.', 'difficulty': 'medium'},

        # Logik & Problemlösung
        {'category': 'Logik', 'question': 'Wenn 5 Maschinen 5 Minuten brauchen, um 5 Produkte herzustellen, wie lange brauchen 100 Maschinen für 100 Produkte?', 'difficulty': 'medium'},
        {'category': 'Logik', 'question': 'Ein Bauer hat 17 Schafe. Alle bis auf 9 sterben. Wie viele bleiben übrig?', 'difficulty': 'easy'},
        {'category': 'Logik', 'question': 'Du hast 3 Türen: hinter einer ist ein Preis, hinter den anderen Ziegen. Du wählst Tür 1. Der Moderator öffnet Tür 3 (Ziege). Solltest du wechseln?', 'difficulty': 'hard'},

        # Wissen & Fakten
        {'category': 'Wissen', 'question': 'Erkläre in einfachen Worten, wie künstliche neuronale Netze funktionieren.', 'difficulty': 'medium'},
        {'category': 'Wissen', 'question': 'Was ist der Unterschied zwischen Machine Learning und Deep Learning?', 'difficulty': 'easy'},
        {'category': 'Wissen', 'question': 'Beschreibe die wichtigsten Prinzipien der Quantenmechanik für Einsteiger.', 'difficulty': 'hard'},

        # Programmierung
        {'category': 'Code', 'question': 'Schreibe eine Python-Funktion, die prüft, ob ein String ein Palindrom ist.', 'difficulty': 'easy'},
        {'category': 'Code', 'question': 'Erkläre den Unterschied zwischen einer Liste und einem Tupel in Python.', 'difficulty': 'easy'},
        {'category': 'Code', 'question': 'Wie würdest du einen einfachen Caching-Mechanismus in Python implementieren?', 'difficulty': 'medium'},

        # Ethik & Philosophie
        {'category': 'Ethik', 'question': 'Sollten KI-Systeme die Möglichkeit haben, ethische Entscheidungen selbst zu treffen?', 'difficulty': 'hard'},
        {'category': 'Ethik', 'question': 'Was bedeutet "fair" im Kontext von maschinellem Lernen?', 'difficulty': 'medium'},
        {'category': 'Ethik', 'question': 'Wie kann man Bias in KI-Systemen verhindern?', 'difficulty': 'medium'},

        # Allgemeinwissen
        {'category': 'Allgemein', 'question': 'Erkläre den Klimawandel in drei Sätzen.', 'difficulty': 'easy'},
        {'category': 'Allgemein', 'question': 'Was sind die Hauptunterschiede zwischen erneuerbaren und nicht-erneuerbaren Energien?', 'difficulty': 'easy'},
        {'category': 'Allgemein', 'question': 'Beschreibe die Funktionsweise des Internets in einfachen Worten.', 'difficulty': 'medium'},

        # Mathematik
        {'category': 'Mathematik', 'question': 'Erkläre den Satz des Pythagoras mit einem praktischen Beispiel.', 'difficulty': 'easy'},
        {'category': 'Mathematik', 'question': 'Was ist eine Fibonacci-Folge und wo findet man sie in der Natur?', 'difficulty': 'medium'},
        {'category': 'Mathematik', 'question': 'Wie berechnet man die Wahrscheinlichkeit von unabhängigen Ereignissen?', 'difficulty': 'medium'},
    ]

    for q in questions:
        db.execute('''
            INSERT INTO benchmark_questions (category, question, difficulty)
            VALUES (?, ?, ?)
        ''', (q['category'], q['question'], q['difficulty']))

    db.commit()
    return jsonify({'success': True, 'count': len(questions)}), 200

@app.route('/api/benchmark/start', methods=['POST'])
@login_required
def start_benchmark():
    """Start a new benchmarking session."""
    data = request.get_json()
    models = data.get('models', [])
    num_questions = data.get('num_questions', 5)

    if len(models) < 2:
        return jsonify({'error': 'Mindestens 2 Modelle erforderlich'}), 400

    # Check if all models are Ollama models
    ollama_models = [m for m in models if m.startswith('ollama:')]
    if len(ollama_models) != len(models):
        return jsonify({'error': 'Alle Modelle müssen Ollama-Modelle sein'}), 400

    db = get_db()

    # Check if questions exist
    question_count = db.execute('SELECT COUNT(*) as count FROM benchmark_questions').fetchone()
    if question_count['count'] == 0:
        return jsonify({'error': 'Keine Fragen verfügbar. Bitte initialisieren Sie den Fragen-Pool.'}), 400

    # Create benchmark session
    cursor = db.execute('''
        INSERT INTO benchmark_sessions (user_id, models, total_questions, completed_questions, status)
        VALUES (?, ?, ?, 0, 'active')
    ''', (current_user.id, json.dumps(models), num_questions))

    session_id = cursor.lastrowid
    db.commit()

    return jsonify({'session_id': session_id}), 201

@app.route('/api/benchmark/session/<int:session_id>/next-question', methods=['GET'])
@login_required
def get_next_benchmark_question(session_id):
    """Get the next question for benchmarking."""
    db = get_db()

    # Verify session ownership
    session = db.execute('''
        SELECT * FROM benchmark_sessions
        WHERE id = ? AND user_id = ?
    ''', (session_id, current_user.id)).fetchone()

    if not session:
        return jsonify({'error': 'Session nicht gefunden'}), 404

    if session['status'] == 'completed':
        return jsonify({'completed': True}), 200

    # Check if session is complete
    if session['completed_questions'] >= session['total_questions']:
        db.execute('''
            UPDATE benchmark_sessions
            SET status = 'completed', completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (session_id,))
        db.commit()
        return jsonify({'completed': True}), 200

    # Get already answered questions
    answered = db.execute('''
        SELECT DISTINCT question_id FROM benchmark_responses
        WHERE session_id = ?
    ''', (session_id,)).fetchall()

    answered_ids = [r['question_id'] for r in answered]

    # Get a random unanswered question
    if answered_ids:
        placeholders = ','.join('?' * len(answered_ids))
        question = db.execute(f'''
            SELECT * FROM benchmark_questions
            WHERE id NOT IN ({placeholders})
            ORDER BY RANDOM()
            LIMIT 1
        ''', answered_ids).fetchone()
    else:
        question = db.execute('''
            SELECT * FROM benchmark_questions
            ORDER BY RANDOM()
            LIMIT 1
        ''').fetchone()

    if not question:
        return jsonify({'error': 'Keine Fragen mehr verfügbar'}), 404

    # Get responses from all models
    models = json.loads(session['models'])
    responses = []

    import time
    import random

    for model in models:
        start_time = time.time()

        # Get AI response
        response_text = get_ai_response(model, question['question'], None)

        response_time = time.time() - start_time

        # Store response
        cursor = db.execute('''
            INSERT INTO benchmark_responses (session_id, question_id, model, response, response_time)
            VALUES (?, ?, ?, ?, ?)
        ''', (session_id, question['id'], model, response_text, response_time))

        response_id = cursor.lastrowid

        responses.append({
            'id': response_id,
            'response': response_text,
            'response_time': response_time
        })

    db.commit()

    # Shuffle responses for blind testing
    random.shuffle(responses)

    return jsonify({
        'question': {
            'id': question['id'],
            'text': question['question'],
            'category': question['category'],
            'difficulty': question['difficulty']
        },
        'responses': responses,
        'progress': {
            'current': session['completed_questions'] + 1,
            'total': session['total_questions']
        }
    }), 200

def get_ai_response_simple(model_service, prompt):
    """Simplified version of get_ai_response for benchmarking without conversation context."""
    db = get_db()

    service = model_service.split(':')[0]
    model_name = model_service.split(':')[1] if ':' in model_service else None

    api_key_row = db.execute('''
        SELECT api_key FROM api_keys
        WHERE user_id = ? AND service = ?
    ''', (current_user.id, service)).fetchone()

    if not api_key_row:
        return f"Error: {service} API key not configured"

    try:
        if service == 'ollama':
            if not model_name:
                return "Error: No model specified for Ollama"
            response = requests.post('http://localhost:11434/api/chat', json={
                'model': model_name,
                'messages': [{'role': 'user', 'content': prompt}],
                'stream': False
            })
            if response.status_code == 200:
                return response.json()['message']['content']
    except Exception as e:
        return f"Error: {str(e)}"

    return "Error: Unable to get response"

@app.route('/api/benchmark/session/<int:session_id>/submit-rating', methods=['POST'])
@login_required
def submit_benchmark_rating(session_id):
    """Submit a rating for a question."""
    data = request.get_json()
    question_id = data.get('question_id')
    selected_response_id = data.get('selected_response_id')

    db = get_db()

    # Verify session ownership
    session = db.execute('''
        SELECT * FROM benchmark_sessions
        WHERE id = ? AND user_id = ?
    ''', (session_id, current_user.id)).fetchone()

    if not session:
        return jsonify({'error': 'Session nicht gefunden'}), 404

    # Store rating
    db.execute('''
        INSERT INTO benchmark_ratings (session_id, question_id, selected_response_id, rating_value)
        VALUES (?, ?, ?, 1)
    ''', (session_id, question_id, selected_response_id))

    # Update session progress
    db.execute('''
        UPDATE benchmark_sessions
        SET completed_questions = completed_questions + 1
        WHERE id = ?
    ''', (session_id,))

    db.commit()

    return jsonify({'success': True}), 200

@app.route('/api/benchmark/session/<int:session_id>/results', methods=['GET'])
@login_required
def get_benchmark_results(session_id):
    """Get the results of a benchmarking session."""
    db = get_db()

    # Verify session ownership
    session = db.execute('''
        SELECT * FROM benchmark_sessions
        WHERE id = ? AND user_id = ?
    ''', (session_id, current_user.id)).fetchone()

    if not session:
        return jsonify({'error': 'Session nicht gefunden'}), 404

    models = json.loads(session['models'])

    # Get all ratings with model information
    ratings = db.execute('''
        SELECT
            br.question_id,
            br.selected_response_id,
            bq.question,
            bq.category,
            bresp.model,
            bresp.response,
            bresp.response_time
        FROM benchmark_ratings br
        JOIN benchmark_questions bq ON br.question_id = bq.id
        JOIN benchmark_responses bresp ON br.selected_response_id = bresp.id
        WHERE br.session_id = ?
    ''', (session_id,)).fetchall()

    # Calculate scores
    model_scores = {model: {'wins': 0, 'total_response_time': 0, 'responses': 0} for model in models}

    for rating in ratings:
        model = rating['model']
        if model in model_scores:
            model_scores[model]['wins'] += 1

    # Get all response times
    all_responses = db.execute('''
        SELECT model, response_time
        FROM benchmark_responses
        WHERE session_id = ?
    ''', (session_id,)).fetchall()

    for resp in all_responses:
        model = resp['model']
        if model in model_scores:
            model_scores[model]['total_response_time'] += resp['response_time'] or 0
            model_scores[model]['responses'] += 1

    # Calculate averages and format results
    results = []
    for model in models:
        score = model_scores[model]
        avg_time = score['total_response_time'] / score['responses'] if score['responses'] > 0 else 0

        results.append({
            'model': model,
            'wins': score['wins'],
            'total_questions': session['total_questions'],
            'win_rate': (score['wins'] / session['total_questions'] * 100) if session['total_questions'] > 0 else 0,
            'avg_response_time': round(avg_time, 2)
        })

    # Sort by wins
    results.sort(key=lambda x: x['wins'], reverse=True)

    # Get category breakdown
    category_stats = db.execute('''
        SELECT
            bq.category,
            bresp.model,
            COUNT(*) as wins
        FROM benchmark_ratings br
        JOIN benchmark_questions bq ON br.question_id = bq.id
        JOIN benchmark_responses bresp ON br.selected_response_id = bresp.id
        WHERE br.session_id = ?
        GROUP BY bq.category, bresp.model
    ''', (session_id,)).fetchall()

    categories = {}
    for stat in category_stats:
        cat = stat['category']
        if cat not in categories:
            categories[cat] = {}
        categories[cat][stat['model']] = stat['wins']

    return jsonify({
        'session': {
            'id': session['id'],
            'created_at': session['created_at'],
            'completed_at': session['completed_at'],
            'total_questions': session['total_questions']
        },
        'overall_results': results,
        'category_breakdown': categories
    }), 200

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5555)