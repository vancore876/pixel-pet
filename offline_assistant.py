"""Small rule-based conversation helpers; no model or network is used."""
import re


class OfflineAssistant:
    def __init__(self, name='Jeffery'):
        self.name = name
        self.user_name = None

    def reply(self, text):
        value = re.sub(r'\s+', ' ', text.strip())
        lower = value.casefold().strip(' .!?')
        match = re.fullmatch(r'my name is ([A-Za-z][A-Za-z\'-]{0,29})[.!]?', value, re.I)
        if match:
            self.user_name = match.group(1)
            return f'Nice to meet you, {self.user_name}. I can remember your name during this conversation.'
        if lower in ('what is my name', "what's my name", 'do you know my name'):
            return (f'You told me your name is {self.user_name}.' if self.user_name else
                    'You can tell me your name by saying “My name is …”.')
        if re.fullmatch(r'(?:hi|hello|hey|good morning|good afternoon|good evening)(?:[, ]+(?:jeffery|jeffrey))?', lower):
            return f'Hello{", " + self.user_name if self.user_name else ""}! What can I help you find today?'
        if lower in ('how are you', 'how are you doing', 'how are you today'):
            return 'I am ready to help. How can I help you today?'
        if lower in ('what is your name', "what's your name", 'who are you'):
            return f'My name is {self.name}. I can look up parts and check the saved chassis list on this computer.'
        if re.search(r'\b(?:thank you|thanks|thank u)\b', lower):
            return 'You are welcome. Let me know if you need another part or chassis check.'
        if lower in ('bye', 'goodbye', 'see you', 'see you later'):
            return 'Goodbye! I will be here when you need me.'
        if lower in ('help', 'what can you do', 'what do you do', 'how do i use this'):
            return ('I can search saved parts by number or description and check whether a chassis number is in the saved list. '
                    'Try “Where is part ABC123?” or “Is chassis ABC12345 on the list?” '
                    'I can also handle simple greetings without an internet connection.')
        return ('I do not understand that yet. I can answer simple greetings and look up a part number, '
                'description, or chassis number from the saved lists. Try asking in a different way.')
