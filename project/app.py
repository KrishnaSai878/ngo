from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect, CSRFError, generate_csrf
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import jwt
import bcrypt
from datetime import datetime, timedelta
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import database models and utilities
from database.models import db, User, NGO, Volunteer, Donor, Event, TimeSlot, Booking, Message, Resource, Project
from database.queries import init_queries

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'mysql+mysqlconnector://root:sai0001sai@localhost/ngoconnect'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280
}
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'your-email@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'your-app-password')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
app.config['WTF_CSRF_ENABLED'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = None

# Security settings
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Initialize database
db.init_app(app)

# Initialize other extensions
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
mail = Mail(app)
socketio = SocketIO(app, cors_allowed_origins="*")
csrf = CSRFProtect(app)

# Initialize queries
queries = init_queries(db, {
    'User': User, 'NGO': NGO, 'Volunteer': Volunteer, 'Donor': Donor,
    'Event': Event, 'TimeSlot': TimeSlot, 'Booking': Booking, 'Message': Message,
    'Resource': Resource, 'Project': Project
})

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Security headers
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # Force HTTPS in production
    if os.environ.get('FLASK_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    
    return response

# Ensure csrf_token() is available in all templates
@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=generate_csrf)

# Friendly CSRF error handler
@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash(f'Form security check failed: {e.description}', 'error')
    return redirect(request.referrer or url_for('index'))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        data = request.form
        email = data.get('email')
        password = data.get('password')
        role = data.get('role')
        first_name = data.get('first_name')
        last_name = data.get('last_name')
        phone = data.get('phone')

        if User.query.filter_by(email=email).first():
            flash('Email already registered')
            return redirect(url_for('register'))

        user = User(
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            first_name=first_name,
            last_name=last_name,
            phone=phone
        )
        db.session.add(user)
        db.session.commit()

        # Create role-specific profile
        if role == 'ngo':
            ngo = NGO(
                user_id=user.id,
                organization_name=data.get('organization_name'),
                description=data.get('description'),
                mission=data.get('mission'),
                website=data.get('website'),
                address=data.get('address'),
                city=data.get('city'),
                state=data.get('state'),
                zip_code=data.get('zip_code'),
                category=data.get('category')
            )
            db.session.add(ngo)
        elif role == 'volunteer':
            # Handle skills and interests - check if they exist in form data
            skills = data.getlist('skills') if 'skills' in data else []
            interests = data.getlist('interests') if 'interests' in data else []
            
            volunteer = Volunteer(
                user_id=user.id,
                bio=data.get('bio'),
                skills=json.dumps(skills),
                interests=json.dumps(interests)
            )
            db.session.add(volunteer)
        elif role == 'donor':
            donor = Donor(
                user_id=user.id,
                company_name=data.get('company_name')
            )
            db.session.add(donor)

        db.session.commit()
        flash('Registration successful! Please login.')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Simple rate limiting - check session for failed attempts
        failed_attempts = session.get('failed_login_attempts', 0)
        if failed_attempts >= 5:
            flash('Too many failed login attempts. Please try again later.', 'error')
            return render_template('login.html')
        
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            # Reset failed attempts on successful login
            session.pop('failed_login_attempts', None)
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('dashboard'))
        else:
            # Increment failed attempts
            session['failed_login_attempts'] = failed_attempts + 1
            flash('Invalid email or password')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# File upload configuration
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx'}
MAX_FILE_SIZE = 16 * 1024 * 1024  # 16MB

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# File upload route
@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    try:
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)
        
        # Check file size
        file.seek(0, os.SEEK_END)
        file_length = file.tell()
        file.seek(0)
        
        if file_length > MAX_FILE_SIZE:
            flash('File too large. Maximum size is 16MB', 'error')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # Add timestamp to prevent filename conflicts
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{filename}"
            
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            flash('File uploaded successfully', 'success')
            return redirect(request.url)
        else:
            flash('Invalid file type. Allowed: PNG, JPG, JPEG, GIF, PDF, DOC, DOCX', 'error')
            return redirect(request.url)
            
    except Exception as e:
        flash(f'File upload failed: {str(e)}', 'error')
        return redirect(request.url)

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif current_user.role == 'ngo':
        return redirect(url_for('ngo_dashboard'))
    elif current_user.role == 'volunteer':
        return redirect(url_for('volunteer_dashboard'))
    elif current_user.role == 'donor':
        return redirect(url_for('donor_dashboard'))
    else:
        flash('Unknown user role. Please contact support.', 'error')
        return redirect(url_for('index'))

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('dashboard'))
    
    # Get platform statistics using queries
    stats = queries.get_platform_stats()
    
    return render_template('admin/dashboard.html', **stats)

@app.route('/ngo/dashboard')
@login_required
def ngo_dashboard():
    if current_user.role != 'ngo':
        flash('Access denied. NGO privileges required.', 'error')
        return redirect(url_for('dashboard'))
    
    ngo = NGO.query.filter_by(user_id=current_user.id).first()
    if not ngo:
        flash('NGO profile not found.', 'error')
        return redirect(url_for('dashboard'))
    
    # Get NGO statistics and data using queries
    stats = queries.get_ngo_stats(ngo.id)
    events = Event.query.filter_by(ngo_id=ngo.id).order_by(Event.created_at.desc()).limit(5).all()
    
    return render_template('ngo/dashboard.html', ngo=ngo, events=events, **stats)

@app.route('/volunteer/dashboard')
@login_required
def volunteer_dashboard():
    if current_user.role != 'volunteer':
        flash('Access denied. Volunteer privileges required.', 'error')
        return redirect(url_for('dashboard'))
    
    volunteer = Volunteer.query.filter_by(user_id=current_user.id).first()
    if not volunteer:
        flash('Volunteer profile not found.', 'error')
        return redirect(url_for('dashboard'))
    
    # Get volunteer statistics and data using queries
    stats = queries.get_volunteer_stats(volunteer.id)
    bookings = queries.get_user_bookings(current_user.id, 'confirmed')[:5]
    recommended_events = queries.get_recommended_events(volunteer.id, 5)
    
    # Get completed events count
    completed_events = Booking.query.filter_by(
        volunteer_id=volunteer.id, 
        status='completed'
    ).count()
    
    return render_template('volunteer/dashboard.html', 
                         volunteer=volunteer, 
                         bookings=bookings, 
                         recommended_events=recommended_events,
                         completed_events=completed_events,
                         **stats)

@app.route('/donor/dashboard')
@login_required
def donor_dashboard():
    if current_user.role != 'donor':
        return redirect(url_for('dashboard'))
    
    donor = Donor.query.filter_by(user_id=current_user.id).first()
    
    return render_template('donor/dashboard.html', donor=donor)

@app.route('/volunteer/achievements')
@login_required
def volunteer_achievements():
    if current_user.role != 'volunteer':
        flash('Access denied. Volunteer privileges required.', 'error')
        return redirect(url_for('dashboard'))

    volunteer = Volunteer.query.filter_by(user_id=current_user.id).first()
    if not volunteer:
        flash('Volunteer profile not found.', 'error')
        return redirect(url_for('dashboard'))

    stats = queries.get_volunteer_stats(volunteer.id)
    recent_bookings = queries.get_user_bookings(current_user.id)[:10]

    return render_template(
        'volunteer/achievements.html',
        volunteer=volunteer,
        recent_bookings=recent_bookings,
        **stats
    )

@app.route('/volunteers/leaderboard')
def volunteers_leaderboard():
    points_leaders = queries.get_volunteer_leaderboard(limit=10)
    hours_leaders = queries.get_hours_leaderboard(limit=10)
    return render_template(
        'volunteers_leaderboard.html',
        points_leaders=points_leaders,
        hours_leaders=hours_leaders
    )

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/ngos')
def ngos_directory():
    search_term = request.args.get('q', '')
    category = request.args.get('category') or None
    city = request.args.get('city') or None
    ngos = queries.search_ngos(search_term, category=category, city=city)
    return render_template('ngos.html', ngos=ngos, q=search_term, category=category, city=city)

@app.route('/ngos/<int:ngo_id>/opportunities')
def ngo_opportunities(ngo_id: int):
    ngo = NGO.query.get_or_404(ngo_id)
    events = Event.query.filter_by(ngo_id=ngo.id, is_active=True).order_by(Event.start_date.asc()).all()
    return render_template('ngo/opportunities.html', ngo=ngo, events=events)

@app.route('/volunteer/events/<int:event_id>')
def volunteer_event_detail(event_id: int):
    event = Event.query.get_or_404(event_id)
    ngo = NGO.query.get(event.ngo_id)
    time_slots = TimeSlot.query.filter_by(event_id=event.id, is_available=True).order_by(TimeSlot.start_time.asc()).all()
    return render_template('volunteer/event_detail.html', event=event, ngo=ngo, time_slots=time_slots)


# NGO Event Management Routes
@app.route('/ngo/events')
@login_required
def ngo_events():
    if current_user.role != 'ngo':
        flash('Access denied. NGO access required.', 'error')
        return redirect(url_for('dashboard'))
    
    ngo = NGO.query.filter_by(user_id=current_user.id).first()
    if not ngo:
        flash('NGO profile not found.', 'error')
        return redirect(url_for('dashboard'))
    
    events = Event.query.filter_by(ngo_id=ngo.id).order_by(Event.created_at.desc()).all()
    return render_template('ngo/events.html', events=events, ngo=ngo)

@app.route('/ngo/events/new', methods=['GET', 'POST'])
@login_required
def ngo_create_event():
    if current_user.role != 'ngo':
        flash('Access denied. NGO access required.', 'error')
        return redirect(url_for('dashboard'))
    
    ngo = NGO.query.filter_by(user_id=current_user.id).first()
    if not ngo:
        flash('NGO profile not found.', 'error')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        try:
            # Parse required skills from form
            required_skills = request.form.getlist('required_skills')
            
            event = Event(
                ngo_id=ngo.id,
                title=request.form['title'],
                description=request.form['description'],
                location=request.form['location'],
                start_date=datetime.strptime(request.form['start_date'], '%Y-%m-%d'),
                end_date=datetime.strptime(request.form['end_date'], '%Y-%m-%d'),
                category=request.form['category'],
                max_volunteers=int(request.form['max_volunteers']),
                required_skills=json.dumps(required_skills),
                is_active=True
            )
            
            db.session.add(event)
            db.session.commit()
            
            # Create time slots for the event
            start_date = event.start_date
            end_date = event.end_date
            current_date = start_date
            
            while current_date <= end_date:
                # Create 2-hour slots from 9 AM to 5 PM
                for hour in range(9, 17, 2):
                    start_time = datetime.combine(current_date, datetime.min.time().replace(hour=hour))
                    end_time = start_time + timedelta(hours=2)
                    
                    time_slot = TimeSlot(
                        event_id=event.id,
                        start_time=start_time,
                        end_time=end_time,
                        max_volunteers=event.max_volunteers,
                        current_volunteers=0,
                        is_available=True
                    )
                    db.session.add(time_slot)
                
                current_date += timedelta(days=1)
            
            db.session.commit()
            flash('Event created successfully!', 'success')
            return redirect(url_for('ngo_events'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating event: {str(e)}', 'error')
    
    return render_template('ngo/create_event.html', ngo=ngo)

@app.route('/ngo/events/<int:event_id>')
@login_required
def ngo_view_event(event_id):
    if current_user.role != 'ngo':
        flash('Access denied. NGO access required.', 'error')
        return redirect(url_for('dashboard'))
    
    ngo = NGO.query.filter_by(user_id=current_user.id).first()
    if not ngo:
        flash('NGO profile not found.', 'error')
        return redirect(url_for('dashboard'))
    
    event = Event.query.get_or_404(event_id)
    if event.ngo_id != ngo.id:
        flash('Access denied. You can only view your own events.', 'error')
        return redirect(url_for('ngo_events'))
    
    time_slots = TimeSlot.query.filter_by(event_id=event.id).order_by(TimeSlot.start_time).all()
    bookings = Booking.query.filter_by(event_id=event.id).all()
    
    return render_template('ngo/view_event.html', event=event, time_slots=time_slots, bookings=bookings, ngo=ngo)

@app.route('/ngo/events/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
def ngo_edit_event(event_id):
    if current_user.role != 'ngo':
        flash('Access denied. NGO access required.', 'error')
        return redirect(url_for('dashboard'))
    
    ngo = NGO.query.filter_by(user_id=current_user.id).first()
    if not ngo:
        flash('NGO profile not found.', 'error')
        return redirect(url_for('dashboard'))
    
    event = Event.query.get_or_404(event_id)
    if event.ngo_id != ngo.id:
        flash('Access denied. You can only edit your own events.', 'error')
        return redirect(url_for('ngo_events'))
    
    if request.method == 'POST':
        try:
            required_skills = request.form.getlist('required_skills')
            
            event.title = request.form['title']
            event.description = request.form['description']
            event.location = request.form['location']
            event.start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d')
            event.end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d')
            event.category = request.form['category']
            event.max_volunteers = int(request.form['max_volunteers'])
            event.required_skills = json.dumps(required_skills)
            event.updated_at = datetime.utcnow()
            
            db.session.commit()
            flash('Event updated successfully!', 'success')
            return redirect(url_for('ngo_view_event', event_id=event.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating event: {str(e)}', 'error')
    
    return render_template('ngo/edit_event.html', event=event, ngo=ngo)

@app.route('/ngo/events/<int:event_id>/delete', methods=['POST'])
@login_required
def ngo_delete_event(event_id):
    if current_user.role != 'ngo':
        flash('Access denied. NGO access required.', 'error')
        return redirect(url_for('dashboard'))
    
    ngo = NGO.query.filter_by(user_id=current_user.id).first()
    if not ngo:
        flash('NGO profile not found.', 'error')
        return redirect(url_for('dashboard'))
    
    event = Event.query.get_or_404(event_id)
    if event.ngo_id != ngo.id:
        flash('Access denied. You can only delete your own events.', 'error')
        return redirect(url_for('ngo_events'))
    
    try:
        # Delete related bookings and time slots first
        Booking.query.filter_by(event_id=event.id).delete()
        TimeSlot.query.filter_by(event_id=event.id).delete()
        
        # Delete the event
        db.session.delete(event)
        db.session.commit()
        
        flash('Event deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting event: {str(e)}', 'error')
    
    return redirect(url_for('ngo_events'))

@app.route('/ngo/events/<int:event_id>/toggle-status', methods=['POST'])
@login_required
def ngo_toggle_event_status(event_id):
    if current_user.role != 'ngo':
        flash('Access denied. NGO access required.', 'error')
        return redirect(url_for('dashboard'))
    
    ngo = NGO.query.filter_by(user_id=current_user.id).first()
    if not ngo:
        flash('NGO profile not found.', 'error')
        return redirect(url_for('dashboard'))
    
    event = Event.query.get_or_404(event_id)
    if event.ngo_id != ngo.id:
        flash('Access denied. You can only modify your own events.', 'error')
        return redirect(url_for('ngo_events'))
    
    try:
        event.is_active = not event.is_active
        db.session.commit()
        
        status = 'activated' if event.is_active else 'deactivated'
        flash(f'Event {status} successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating event status: {str(e)}', 'error')
    
    return redirect(url_for('ngo_view_event', event_id=event.id))

# API Routes
@app.route('/api/events')
def get_events():
    events = Event.query.filter_by(is_active=True).all()
    return jsonify([{
        'id': event.id,
        'title': event.title,
        'description': event.description,
        'location': event.location,
        'start_date': event.start_date.isoformat(),
        'end_date': event.end_date.isoformat(),
        'ngo_name': NGO.query.get(event.ngo_id).organization_name
    } for event in events])

@app.route('/api/events/<int:event_id>/slots')
def get_event_slots(event_id):
    slots = TimeSlot.query.filter_by(event_id=event_id, is_available=True).all()
    return jsonify([{
        'id': slot.id,
        'start_time': slot.start_time.isoformat(),
        'end_time': slot.end_time.isoformat(),
        'available_spots': slot.max_volunteers - slot.current_volunteers
    } for slot in slots])

@app.route('/api/book-slot', methods=['POST'])
@login_required
def book_slot():
    if current_user.role != 'volunteer':
        return jsonify({'error': 'Only volunteers can book slots'}), 403
    
    try:
        data = request.json
        slot_id = data.get('slot_id')
        event_id = data.get('event_id')
        
        if not slot_id or not event_id:
            return jsonify({'error': 'Missing slot_id or event_id'}), 400
        
        volunteer = Volunteer.query.filter_by(user_id=current_user.id).first()
        if not volunteer:
            return jsonify({'error': 'Volunteer profile not found'}), 400
        
        # Check if already booked
        existing_booking = Booking.query.filter_by(
            volunteer_id=volunteer.id,
            time_slot_id=slot_id
        ).first()
        
        if existing_booking:
            return jsonify({'error': 'You have already booked this slot'}), 400
        
        # Use database transaction to prevent race condition
        with db.session.begin():
            # Re-fetch slot with row locking
            slot = TimeSlot.query.with_for_update().get(slot_id)
            
            if not slot or not slot.is_available:
                return jsonify({'error': 'Slot not available'}), 400
            
            if slot.current_volunteers >= slot.max_volunteers:
                return jsonify({'error': 'Slot is full'}), 400
            
            booking = Booking(
                volunteer_id=volunteer.id,
                time_slot_id=slot_id,
                event_id=event_id,
                status='confirmed'
            )
            
            slot.current_volunteers += 1
            if slot.current_volunteers >= slot.max_volunteers:
                slot.is_available = False
            
            db.session.add(booking)
            # Commit happens automatically with the 'with' block
        
        return jsonify({'message': 'Slot booked successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Booking failed: {str(e)}'}), 500

# Socket.IO events
@socketio.on('join_room')
def on_join(data):
    room = data['room']
    join_room(room)
    emit('status', {'msg': f'User has joined the room: {room}'}, room=room)

@socketio.on('send_message')
def handle_message(data):
    room = data['room']
    try:
        message = Message(
            sender_id=current_user.id,
            receiver_id=data['receiver_id'],
            content=data['message']
        )
        db.session.add(message)
        db.session.commit()
        
        emit('receive_message', {
            'sender': current_user.first_name + ' ' + current_user.last_name,
            'message': data['message'],
            'timestamp': datetime.utcnow().isoformat()
        }, room=room)
    except Exception as e:
        db.session.rollback()
        emit('error', {'message': f'Failed to send message: {str(e)}'}, room=room)

if __name__ == '__main__':
    print("Starting NGO Connect Platform...")
    try:
        with app.app_context():
            print("Creating database tables...")
            db.create_all()
            print("Database tables created successfully!")
        print("Starting server on http://127.0.0.1:5000")
        # Use debug=False for production, debug=True for development
        debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
        socketio.run(app, host='127.0.0.1', port=5000, debug=debug_mode)
    except Exception as e:
        print(f"Error starting application: {e}")
        import traceback
        traceback.print_exc()
