import os
import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'secret-key-pour-le-dev!')
socketio = SocketIO(app, cors_allowed_origins="*")

# Base de données temporaire en mémoire
ROOMS = {} 
# Mots pour le jeu
WORDS = ["Chat", "Chien", "Avion", "Pizza", "Soleil", "Voiture", "Guitare", "Maison", "Arbre", "Ordinateur"]

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('join')
def on_join(data):
    username = data['username']
    room = data['room']
    
    if room not in ROOMS:
        ROOMS[room] = {
            'players': {},
            'word': random.choice(WORDS),
            'game_started': False,
            'colors': ['#FF5733', '#33FF57', '#3357FF', '#F3FF33', '#FF33F3', '#33FFF0']
        }
    
    if ROOMS[room]['game_started']:
        emit('error', {'msg': 'La partie a déjà commencé dans ce salon.'})
        return

    join_room(room)
    
    # Assigner une couleur unique au joueur
    color_index = len(ROOMS[room]['players']) % len(ROOMS[room]['colors'])
    player_color = ROOMS[room]['colors'][color_index]
    
    ROOMS[room]['players'][request.sid] = {
        'username': username,
        'color': player_color,
        'role': 'Innocent'
    }
    
    emit('room_state', {'players': list(ROOMS[room]['players'].values()), 'game_started': False}, to=room)

@socketio.on('start_game')
def on_start_game(data):
    room = data['room']
    if room in ROOMS and not ROOMS[room]['game_started']:
        ROOMS[room]['game_started'] = True
        players_sids = list(ROOMS[room]['players'].keys())
        
        if len(players_sids) < 2:
            emit('error', {'msg': 'Il faut au moins 2 joueurs pour lancer.'}, to=request.sid)
            return
            
        # Désigner l'imposteur (Undercover)
        impostor_sid = random.choice(players_sids)
        ROOMS[room]['players'][impostor_sid]['role'] = 'Imposteur'
        
        # Envoyer le rôle en privé à chaque joueur
        for sid, player in ROOMS[room]['players'].items():
            if sid == impostor_sid:
                emit('game_init', {'role': 'Imposteur', 'word': '??? (Tu es l\'intrus !)', 'color': player['color']}, to=sid)
            else:
                emit('game_init', {'role': 'Innocent', 'word': ROOMS[room]['word'], 'color': player['color']}, to=sid)
        
        emit('chat_msg', {'username': 'Système', 'msg': 'La partie commence ! Dessinez à tour de rôle et démasquez l\'intrus.'}, to=room)

@socketio.on('draw')
def on_draw(data):
    # Relayer le dessin à tout le salon en incluant la couleur du joueur
    room = data['room']
    player_color = ROOMS[room]['players'][request.sid]['color']
    
    emit('draw_response', {
        'x': data['x'], 'y': data['y'], 
        'prevX': data['prevX'], 'prevY': data['prevY'], 
        'color': player_color
    }, to=room, include_self=False)

@socketio.on('message')
def on_message(data):
    room = data['room']
    username = ROOMS[room]['players'][request.sid]['username']
    emit('chat_msg', {'username': username, 'msg': data['msg']}, to=room)

@socketio.on('disconnect')
def on_disconnect():
    # Nettoyage si un joueur quitte
    for room, room_data in list(ROOMS.items()):
        if request.sid in room_data['players']:
            del room_data['players'][request.sid]
            if not room_data['players']:
                del ROOMS[room]
            else:
                emit('room_state', {'players': list(room_data['players'].values()), 'game_started': room_data['game_started']}, to=room)
            break

if __name__ == '__main__':
    socketio.run(app, debug=True)
