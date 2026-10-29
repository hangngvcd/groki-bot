"""Small local recovery journal, no image data or child transcripts."""
import json
import os
from pathlib import Path
import tempfile
from .parent_settings import private_directory


class RecoveryStore:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv('STACKCHAN_STATE_DIR', '~/.stackchan')).expanduser()
        self.path = self.root / 'parent_recovery.json'

    def read(self):
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {'sequence': 0, 'events': [], 'photos': []}

    def save(self, state):
        private_directory(self.root)
        fd, name = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(state, stream)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def record(self, *, item, path, notified):
        state = self.read()
        state['sequence'] += 1
        ident = str(state['sequence'])
        state['events'].append({'id': ident, 'kind': 'homework_complete', 'item': item,
                                'parent_notified': notified, 'photo_id': ident if path else None})
        if path:
            state['photos'].append({'id': ident, 'item': item, 'path': str(path), 'done': False})
        state['events'] = state['events'][-500:]
        state['photos'] = state['photos'][-500:]
        self.save(state)

    def help_event(self, notified):
        state = self.read()
        state['sequence'] += 1
        state['events'].append({'id': str(state['sequence']), 'kind': 'help_notify',
                                'parent_notified': notified})
        state['events'] = state['events'][-500:]
        self.save(state)

    def photos(self):
        return [{**p, 'available': Path(p['path']).is_file()} for p in self.read()['photos']]

    def photo(self, ident, done=False):
        state = self.read()
        for photo in state['photos']:
            if photo['id'] == ident:
                if done:
                    photo['done'] = True
                    self.save(state)
                return {**photo, 'available': Path(photo['path']).is_file()}
        raise LookupError('photo not found')

    def events(self, after=0):
        return [e for e in self.read()['events'] if int(e['id']) > after]
