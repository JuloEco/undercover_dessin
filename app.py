from flask import Flask, render_template, request, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import uuid
import string

app = Flask(__name__)
app.config['SECRET_KEY'] = 'suspekt_secret_2024'
socketio = SocketIO(app, cors_allowed_origins="*")

# ─── Data ───────────────────────────────────────────────────────────────────

WORD_PAIRS = [
    ("Chien", "Chat"),
    ("Voiture", "Moto"),
    ("Soleil", "Lune"),
    ("Château", "Maison"),
    ("Dragon", "Dinosaure"),
    ("Pizza", "Tarte"),
    ("Requin", "Dauphin"),
    ("Robot", "Humain"),
    ("Forêt", "Jungle"),
    ("Avion", "Fusée"),
    ("Fantôme", "Vampire"),
    ("Arc-en-ciel", "Aurore boréale"),
    ("Cactus", "Sapin"),
    ("Licorne", "Cheval"),
    ("Sous-marin", "Bateau"),
]

PLAYER_COLORS = [
    "#FF6B6B", "#4ECDC4", "#FFE66D", "#A8E6CF",
    "#FF8B94", "#7EC8E3", "#F7DC6F", "#BB8FCE",
    "#85C1E9", "#F0B27A", "#82E0AA", "#F1948A",
]

rooms = {}  # room_id -> room state


def generate_room_id():
    return ''.join(random.choices(string.ascii_uppercase, k=5))


def get_room(room_id):
    return rooms.get(room_id)


def create_room(room_id):
    rooms[room_id] = {
        "id": room_id,
        "players": {},        # sid -> player info
        "host": None,
        "phase": "lobby",     # lobby | drawing | voting | results
        "word_pair": None,
        "undercover_sid": None,
        "strokes": [],        # all drawing strokes
        "votes": {},          # voter_sid -> voted_sid
        "eliminated": [],
        "round": 0,
        "current_drawer_index": 0,
        "draw_order": [],
        "turn_timer": None,
    }
    return rooms[room_id]


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('undercover.html')


@app.route('/room/<room_id>')
def room(room_id):
    return render_template('game.html', room_id=room_id)


# ─── Socket Events ───────────────────────────────────────────────────────────

@socketio.on('create_room')
def on_create_room(data):
    # On génère juste le salon dans la mémoire globale
    room_id = generate_room_id()
    while room_id in rooms:
        room_id = generate_room_id()

    create_room(room_id)
    # On renvoie la réponse pour rediriger, sans stocker le sid ici
    emit('room_created', {"room_id": room_id})

@socketio.on('join_room_event')
def on_join_room(data):
    room_id = data.get('room_id', '').upper().strip()
    nickname = data.get('nickname', 'Anonyme').strip()[:20]

    room = get_room(room_id)
    if not room:
        emit('error', {"message": "Salon introuvable !"})
        return
    if room["phase"] != "lobby":
        emit('error', {"message": "La partie a déjà commencé !"})
        return
    if len(room["players"]) >= 8:
        emit('error', {"message": "Salon plein (8 joueurs max) !"})
        return

    # L'accueil reçoit le feu vert pour rediriger
    emit('joined_room', {"room_id": room_id, "nickname": nickname})

@socketio.on('register_player')
def on_register_player(data):
    room_id = data.get('room_id', '').upper().strip()
    nickname = data.get('nickname', 'Anonyme').strip()[:20]
    
    room = get_room(room_id)
    if not room:
        emit('error', {"message": "Salon introuvable au chargement !"})
        return
        
    # Si le salon n'a pas encore de Host, le premier arrivé devient le Host
    if not room["host"]:
        room["host"] = request.sid
        color = PLAYER_COLORS[0]
    else:
        color_index = len(room["players"]) % len(PLAYER_COLORS)
        color = PLAYER_COLORS[color_index]
        
    player = {
        "sid": request.sid,
        "nickname": nickname,
        "color": color,
        "word": None,
        "is_undercover": False,
        "eliminated": False,
    }
    
    room["players"][request.sid] = player
    join_room(room_id)
    
    # On valide l'entrée du joueur et on met à jour tout le monde
    emit('registration_success', {"player": player, "is_host": (room["host"] == request.sid)})
    emit('room_update', _room_public(room), to=room_id)

@socketio.on('start_game')
def on_start_game(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room:
        return
    if room["host"] != request.sid:
        emit('error', {"message": "Seul le chef de salon peut démarrer !"})
        return
    if len(room["players"]) < 3:
        emit('error', {"message": "Il faut au moins 3 joueurs !"})
        return

    # Assign words
    pair = random.choice(WORD_PAIRS)
    room["word_pair"] = pair
    sids = list(room["players"].keys())
    undercover_sid = random.choice(sids)
    room["undercover_sid"] = undercover_sid

    for sid in sids:
        room["players"][sid]["is_undercover"] = (sid == undercover_sid)
        room["players"][sid]["word"] = pair[1] if sid == undercover_sid else pair[0]
        room["players"][sid]["eliminated"] = False

    # Drawing order
    random.shuffle(sids)
    room["draw_order"] = sids
    room["current_drawer_index"] = 0
    room["phase"] = "drawing"
    room["strokes"] = []
    room["round"] = 1

    # Send each player their word privately
    for sid, player in room["players"].items():
        socketio.emit('your_word', {"word": player["word"], "is_undercover": False}, to=sid)

    # Notify game start
    emit('game_started', {
        "draw_order": [room["players"][s]["nickname"] for s in room["draw_order"]],
        "phase": "drawing",
    }, to=room_id)

    _next_turn(room_id)


@socketio.on('draw_stroke')
def on_draw_stroke(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room or room["phase"] != "drawing":
        return

    current_sid = room["draw_order"][room["current_drawer_index"]]
    if request.sid != current_sid:
        return  # Not your turn

    stroke = {
        "points": data.get("points", []),
        "color": room["players"][request.sid]["color"],
        "size": max(1, min(30, data.get("size", 4))),
        "player_sid": request.sid,
        "nickname": room["players"][request.sid]["nickname"],
    }
    room["strokes"].append(stroke)
    emit('new_stroke', stroke, to=room_id, include_self=False)


@socketio.on('end_turn')
def on_end_turn(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room or room["phase"] != "drawing":
        return

    current_sid = room["draw_order"][room["current_drawer_index"]]
    if request.sid != current_sid:
        return

    active_players = [s for s in room["draw_order"]
                      if not room["players"][s]["eliminated"]]
    idx = room["current_drawer_index"]
    current_pos = active_players.index(current_sid) if current_sid in active_players else -1

    next_pos = current_pos + 1
    if next_pos >= len(active_players):
        # All active players drew → start voting
        room["phase"] = "voting"
        room["votes"] = {}
        emit('start_voting', {
            "players": _players_public(room),
            "strokes": room["strokes"],
        }, to=room_id)
    else:
        next_sid = active_players[next_pos]
        room["current_drawer_index"] = room["draw_order"].index(next_sid)
        _next_turn(room_id)


@socketio.on('cast_vote')
def on_cast_vote(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room or room["phase"] != "voting":
        return
    if request.sid in room["votes"]:
        return  # Already voted

    voted_sid = data.get('voted_sid')
    if voted_sid not in room["players"]:
        return
    if voted_sid == request.sid:
        emit('error', {"message": "Vous ne pouvez pas voter contre vous-même !"})
        return

    room["votes"][request.sid] = voted_sid

    # Broadcast vote count update
    vote_counts = _count_votes(room)
    emit('vote_update', {
        "votes_cast": len(room["votes"]),
        "total_voters": len([s for s in room["players"] if not room["players"][s]["eliminated"]]),
        "vote_counts": vote_counts,
    }, to=room_id)

    # Check if all active players voted
    active = [s for s in room["players"] if not room["players"][s]["eliminated"]]
    if len(room["votes"]) >= len(active):
        _resolve_votes(room_id)


@socketio.on('play_again')
def on_play_again(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room:
        return
    if room["host"] != request.sid:
        return

    # Reset to lobby
    for sid in room["players"]:
        room["players"][sid]["word"] = None
        room["players"][sid]["is_undercover"] = False
        room["players"][sid]["eliminated"] = False
    room["phase"] = "lobby"
    room["strokes"] = []
    room["votes"] = {}
    room["undercover_sid"] = None
    room["draw_order"] = []
    room["current_drawer_index"] = 0

    emit('back_to_lobby', _room_public(room), to=room_id)


@socketio.on('disconnect')
def on_disconnect():
    # On cherche dans quelle room était le joueur déconnecté
    for room_id, room in list(rooms.items()):
        if request.sid in room["players"]:
            # On retire le joueur de la mémoire
            player = room["players"].pop(request.sid)
            leave_room(room_id)
            
            # Si le salon est complètement vide, on le supprime
            if not room["players"]:
                rooms.pop(room_id, None)
            else:
                # Si c'était le créateur qui est parti, on donne la couronne à quelqu'un d'autre
                if room["host"] == request.sid:
                    room["host"] = list(room["players"].keys())[0]
                
                # IMPORTANT : On prévient les autres joueurs restants
                socketio.emit('room_update', _room_public(room), to=room_id)
            break


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _next_turn(room_id):
    room = get_room(room_id)
    if not room:
        return
    current_sid = room["draw_order"][room["current_drawer_index"]]
    current_player = room["players"][current_sid]
    active_players = [s for s in room["draw_order"] if not room["players"][s]["eliminated"]]
    pos = active_players.index(current_sid) + 1 if current_sid in active_players else 0

    socketio.emit('your_turn', {
        "sid": current_sid,
        "nickname": current_player["nickname"],
        "color": current_player["color"],
        "position": pos,
        "total": len(active_players),
    }, to=room_id)


def _count_votes(room):
    counts = {}
    for voted_sid in room["votes"].values():
        counts[voted_sid] = counts.get(voted_sid, 0) + 1
    return {room["players"][s]["nickname"]: c for s, c in counts.items() if s in room["players"]}


def _resolve_votes(room_id):
    room = get_room(room_id)
    votes = room["votes"]
    if not votes:
        return

    # Count votes per player
    tally = {}
    for voted_sid in votes.values():
        tally[voted_sid] = tally.get(voted_sid, 0) + 1

    max_votes = max(tally.values())
    candidates = [s for s, c in tally.items() if c == max_votes]
    eliminated_sid = random.choice(candidates)  # tie-break random

    room["players"][eliminated_sid]["eliminated"] = True
    room["eliminated"] = room.get("eliminated", []) + [eliminated_sid]

    is_undercover = (eliminated_sid == room["undercover_sid"])
    undercover_sid = room["undercover_sid"]
    undercover_name = room["players"][undercover_sid]["nickname"] if undercover_sid in room["players"] else "?"

    active = [s for s in room["players"] if not room["players"][s]["eliminated"]]

    # Win conditions
    if is_undercover:
        # Undercover caught → civilians win
        result = "civilians_win"
    elif len(active) <= 2:
        # Too few players left, undercover wins
        result = "undercover_wins"
    else:
        result = None

    if result:
        room["phase"] = "results"
        socketio.emit('game_over', {
            "result": result,
            "eliminated_name": room["players"][eliminated_sid]["nickname"],
            "undercover_name": undercover_name,
            "undercover_color": room["players"][undercover_sid]["color"] if undercover_sid in room["players"] else "#fff",
            "word_civilians": room["word_pair"][0],
            "word_undercover": room["word_pair"][1],
            "strokes": room["strokes"],
            "players": _players_public(room),
            "is_host": False,  # handled client-side
            "host_sid": room["host"],
        }, to=room_id)
    else:
        # Continue drawing without eliminated player
        room["phase"] = "drawing"
        room["votes"] = {}
        socketio.emit('player_eliminated', {
            "eliminated_name": room["players"][eliminated_sid]["nickname"],
            "eliminated_color": room["players"][eliminated_sid]["color"],
            "was_undercover": False,
        }, to=room_id)

        # Next round
        active_order = [s for s in room["draw_order"] if not room["players"][s]["eliminated"]]
        if active_order:
            room["current_drawer_index"] = room["draw_order"].index(active_order[0])
            _next_turn(room_id)


def _room_public(room):
    return {
        "id": room["id"],
        "phase": room["phase"],
        "host": room["host"],
        "players": _players_public(room),
    }


def _players_public(room):
    return [
        {
            "sid": p["sid"],
            "nickname": p["nickname"],
            "color": p["color"],
            "eliminated": p["eliminated"],
        }
        for p in room["players"].values()
    ]


if __name__ == '__main__':
    socketio.run(app, debug=True, port=5001, host='0.0.0.0')
