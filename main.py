import asyncio
import logging
import json
from pywebio.input import *
from pywebio.output import *
from pywebio.session import run_js
from pywebio.session import defer_call
from pywebio.session import run_async
from pywebio.platform.tornado_http import webio_handler
from tornado.web import StaticFileHandler, Application
import tornado.ioloop
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

OAI_key = ""

chat = [] # Chat history
users = set() # Online users

conversation = [] 
history_conversation = {"history": conversation}

now = ""
prev = ""

characters = 0


def getOpenAIKey(): # Get the OpenAI key
    global OAI_key
    try:
        with open("config.json", "r") as json_file:
            data = json.load(json_file)
        OAI_key = data["apikeys"][0]["OAI_key"]
        

    except FileNotFoundError:
        print("Unable to open JSON file.")
        exit()


def getMessage(): # Get the message with history
    message = [{"role": "system", "content": "Below is the conversation history.\n"}]
    global conversation
    global history_conversation

    try:
        with open("conversation.json", "r") as f:
            data = json.load(f)
            conversation = data.get("history", [])

    except FileNotFoundError:
        print("conversation.json file not found.")
        conversation = []
        history_conversation = {"history": conversation}

    for m in history_conversation["history"][:-1]:
        message.append(m)

    # Add the last message to the message list
    if history_conversation:
        message.append({"role": "system", "content": "This is the last message.\n"})
        message.append(history_conversation["history"][-1])

    return message


async def chatroom(): # Main chatroom page
    global chat
    global users
    
    put_html('<div class="chatroom-container">')

    put_markdown("## Welcome to the Chatroom!")

    msg_box = output()
    put_scrollable(msg_box, height=300, keep_bottom=True)

    put_html('</div>')

    put_markdown("### Online Users")
    with use_scope('user_list', clear=True):
        put_text("No users online")

    nickname = await input(
        "Your Nickname",
        required=True,
        placeholder="Enter your nickname",
        validate=lambda n: 'Nickname already exists' if n in users else None
    )
    users.add(nickname)
    logging.info(f"{nickname} joined the chatroom.")
    chat.append((f"**{nickname}**", "joined the chatroom"))
    msg_box.append(put_markdown(f"**{nickname}** joined the chatroom"))

    def remove_user(): # Remove the user from the chatroom
        users.discard(nickname)
        chat.append((f"**{nickname}**", "left the chatroom"))
        logging.info(f"{nickname} left the chatroom.")
        
        with use_scope('user_list', clear=True):
            if users:
                put_markdown("\n".join(f"- {user}" for user in sorted(users)))
            else:
                put_text("No users online")

    defer_call(remove_user)

    loop = run_async(chat_updater(nickname, msg_box)) # Keep the chat updated for the user
    user_list_loop = run_async(update_user_list()) # Update the online user list

    try:
        while True:
            data = await input_group("Send message", [
                input(name='msg', placeholder="Type your message..."),
                file_upload(name='file', placeholder="Upload a file", accept="*"),
                actions(name='command', buttons=['Send', {'label': "Exit Chat", 'type': 'cancel', "color": "danger"}])
            ])

            if data is None:
                break
            
            file_link = None
            if data['file']:
                file_name = data['file']['filename']
                file_content = data['file']['content']
                with open (f"shared_files/{file_name}", "wb") as f:
                    f.write(file_content)

                # Share the file link in the chat
                file_link = f"[Download {file_name}](shared_files/{file_name})"
                chat.append((nickname, f"shared a file: {file_name}"))
                msg_box.append(put_markdown(f"**{nickname}** shared a file: {file_link}"))
                logging.info(f"{nickname} shared a file: {file_name}")
                continue

            if data['msg'] and data['msg'].strip():
                message = data['msg'].strip()

                chat.append((nickname, message))
                conversation.append({"role": nickname, "content": message})
                history_conversation["history"] = conversation

                with open("conversation.json", "w", encoding="utf-8") as f:
                    json.dump(history_conversation, f, indent=4)

                msg_box.append(put_markdown(f"**{nickname}**: {message}"))
                logging.info(f"{nickname}: {message}")

                # If the message pings the AI
                if message.startswith('@AI '):
                    ai_message = message[4:].strip()
                    response = await ai_response(nickname, ai_message)
                    chat.append(("AI", response))
                    logging.info(f"AI: {response}")


    finally: # When the user leaves the chatroom
        loop.close()
        user_list_loop.close()
        users.discard(nickname)
        chat.append((f"**{nickname}**", "left the chatroom"))
        msg_box.append(put_markdown(f"**{nickname}** left the chatroom"))
        logging.info(f"{nickname} left the chatroom.")
        toast("You left the chatroom.")

        with use_scope('user_list', clear=True):
            put_text("Not in chatroom")

        put_buttons(['Reload'], onclick=lambda _: run_js('window.location.reload()'))

        put_buttons([
        {'label': 'Export Chat as JSON', 'value': 'json', 'color': 'success'},
        {'label': 'Export Chat as TXT', 'value': 'txt', 'color': 'info'}
        ], onclick=lambda btn: export_conversation(btn))


async def chat_updater(nickname, msg_box): # Keep the chat updated for the user
    global chat
    last_idx = len(chat)

    while True:
        await asyncio.sleep(1)
    
        for message in chat[last_idx:]:
            if message[0] != nickname:
                msg_box.append(put_markdown(f"**{message[0]}**: {message[1]}"))

        if len(chat) > 100:
            chat = chat[-50:]

        last_idx = len(chat)


def export_conversation(format='json'): # Export the conversation
    """Export full chat history."""
    global chat
    if format == 'json':
        filename = "chat_history.json"
        content = [{"user": user, "message": msg} for user, msg in chat]
        json_content = json.dumps(content, indent=4)
        
        run_js(f"""
            const blob = new Blob([{json.dumps(json_content)}], {{type: "application/json"}});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = "{filename}";
            a.click();
            URL.revokeObjectURL(url);
        """)
    elif format == 'txt':
        filename = "chat_history.txt"
        text_content = "\n".join([f"{user}: {msg}" for user, msg in chat])
        
        run_js(f"""
            const blob = new Blob([{json.dumps(text_content)}], {{type: "text/plain"}});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = "{filename}";
            a.click();
            URL.revokeObjectURL(url);
        """)


async def ai_response(nickname, message): # Get the AI response
    global now
    global prev
    global conversation
    global history_conversation
    global characters

    now = message
    
    if now != prev:
        conversation.append({"role": nickname, "content": now})
        prev = now

    characters = sum(len(d['content']) for d in conversation)

    while characters > 2000:
        try:
            conversation.pop(2)
            characters = sum(len(d['content']) for d in conversation)
        except Exception as e:
            print("Error in popping older messages: " + str(e))

    message_text = getMessage()

    client = OpenAI(api_key=OAI_key)
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "max_tokens": 32,
                "content": "This is how an assistant responded in a conversation with multiple chatters. She would respond in a friendly manner. She would talk about the message and would elaborate on it. Only 32 tokens, so he can't speak much per message."
            },
            {
                "role": "user",
                "content": f"\n----------\n{message_text}\n----------\n"
            }
        ]
    )
    
    try:
        response_text = completion.choices[0].message.content

        response_text_for_history = (f"assistant responded: {response_text}")
        conversation.append({"role": "assistant", "content": response_text_for_history})
        history_conversation["history"] = conversation

        with open("conversation.json", "w", encoding="utf-8") as f:
            json.dump(history_conversation, f, indent=4)

        return response_text
    
    except Exception as e:
        print("Error in text generator: " + str(e))
        return "Error in text generator"


async def update_user_list(): # Update the online user list
    while True:
        await asyncio.sleep(1)
        with use_scope('user_list', clear=True):
            if users:
                put_markdown("\n".join(f"- {user}" for user in sorted(users)))
            else:
                put_text("No users online")


def main():
    routes = [
        (r"/shared_files/(.*)", StaticFileHandler, {"path": "shared_files"}),
        (r"/", webio_handler(chatroom)),
    ]
    
    # Create Tornado Application
    app = Application(routes)
    app.listen(8080)
    print("Server running at http://localhost:8080/")
    
    tornado.ioloop.IOLoop.current().start()

if __name__ == "__main__":
    getOpenAIKey()
    main()
    