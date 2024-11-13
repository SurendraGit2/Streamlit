from langchain_community.llms import Ollama
from langchain.callbacks.manager import CallbackManager
from langchain.schema import HumanMessage
import streamlit as st
from datetime import datetime
import snowflake.connector
from langchain.callbacks.base import BaseCallbackHandler
from db_connection import connection_parameters
 

# Initialize Snowflake connection
def init_snowflake_connection():
    conn = snowflake.connector.connect(
        user=connection_parameters['user'],
        password=connection_parameters['password'],
        account=connection_parameters['account'],
        warehouse=connection_parameters['warehouse'],
        database=connection_parameters['database'],
        schema=connection_parameters['schema']
    )
    return conn

# Save new session to chat_sessions table and return session_id
def create_session(session_name):
    conn = init_snowflake_connection()
    try:
        cursor = conn.cursor()
        
        # Check if session name already exists
        cursor.execute("SELECT session_id FROM chat_sessions WHERE session_name = %s", (session_name,))
        existing_session = cursor.fetchone()
        
        if existing_session:
            st.error("A session with this name already exists. Please choose a different name.")
            return None  # Indicate that session creation was skipped
        
        # Create new session if name doesn't exist
        cursor.execute("INSERT INTO chat_sessions (session_name) VALUES (%s)", (session_name,))
        cursor.execute("SELECT session_id FROM chat_sessions ORDER BY session_id DESC LIMIT 1")
        session_id = cursor.fetchone()[0]
        return session_id

    finally:
        conn.close()

# Save message to chat_history table, including model name
def save_message(session_id, role, content):
    conn = init_snowflake_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_history (session_id, role, content) VALUES (%s, %s, %s)", 
                       (session_id, role, content))
    finally:
        conn.close()


# Retrieve chat history for a given session
def get_chat_history(session_id):
    conn = init_snowflake_connection()
    cursor = conn.cursor()
    query = """
        SELECT timestamp, role, content 
        FROM chat_history 
        WHERE session_id = %s 
        ORDER BY timestamp
    """
    cursor.execute(query, (session_id,))
    chat_data = cursor.fetchall()
    return chat_data


# Custom callback handler to stream response directly in Streamlit and capture full response
class StreamlitCallbackHandler(BaseCallbackHandler):
    def __init__(self, placeholder):
        self.placeholder = placeholder
        self.response_text = ""

    def on_llm_new_token(self, token: str, **kwargs):
        self.response_text += token
        self.placeholder.write(self.response_text)  # Stream response to UI

    def get_full_response(self):
        return self.response_text

st.header("💬 Interactive AI Chatbot for Seamless Conversations")

model_options = ["llama3.2:1b", "llama3.2:3b", "llama3.1:8b","falcon:7b","HridaAI/hrida-t2sql-128k:latest", "other_model"]
selected_model = st.selectbox("Select Model", model_options, index=0)

# Function to initialize the selected model
def initialize_model(selected_model, callback_manager):
    if selected_model in model_options:
        return Ollama(model=selected_model, callback_manager=callback_manager)
    else:
        st.error(f"The model '{selected_model}' is not available locally. Please select a different model.")
        return None
    

if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "session_name" not in st.session_state:
    st.session_state.session_name = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
 
# Sidebar for session management
session_name_input = st.sidebar.text_input("New Session Name", value="Default Session Name")
if st.sidebar.button("Start New Session") and session_name_input:
    session_id = create_session(session_name_input)
    if session_id:  # Proceed only if a new session was created
        st.session_state.session_name = session_name_input
        st.session_state.session_id = session_id
        st.session_state.chat_history = []


# Load past sessions
conn = init_snowflake_connection()
try:
    past_sessions = conn.cursor().execute("SELECT session_id, session_name FROM chat_sessions ORDER BY session_id DESC").fetchall()
finally:
    conn.close()

selected_session_name = st.sidebar.selectbox("Select Previous Session", 
                                        [session[1] for session in past_sessions] if past_sessions else ["No sessions found"], 
                                        index=0)

# Load selected session when the dropdown changes
selected_session = next((session for session in past_sessions if session[1] == selected_session_name), None)
if selected_session and selected_session[0] != st.session_state.session_id:
    st.session_state.session_id = selected_session[0]
    st.session_state.session_name = selected_session_name
    st.session_state.chat_history = get_chat_history(st.session_state.session_id)

# Edit session name
if selected_session:
    new_name = st.sidebar.text_input("Edit Session Name", value=selected_session_name)
    if st.sidebar.button("Save Changes") and new_name != selected_session_name:
        try:
            conn = init_snowflake_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE chat_sessions SET session_name = %s WHERE session_id = %s", (new_name, selected_session[0]))
            st.success("Session name updated successfully.")
            st.session_state.session_name = new_name
        finally:
            conn.close()
            st.rerun()

# Delete session
if selected_session:
    if st.sidebar.button("Delete Session  🗑"):
        try:
            conn = init_snowflake_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_sessions WHERE session_id = %s", (selected_session[0],))
            cursor.execute("DELETE FROM chat_history WHERE session_id = %s", (selected_session[0],))
            st.success("Session deleted successfully.")
            st.session_state.session_id = None
            st.session_state.session_name = ""
            st.session_state.chat_history = []
        finally:
            conn.close()
            st.rerun()

#st.subheader(f"Chat Session: {st.session_state.session_name}")
for entry in st.session_state.chat_history:
    timestamp, role, content = entry  # Adjusted to expect three columns

    if role.lower() == "user":
        with st.chat_message("User"):
            st.write(content)
    else:
        # Treat role as model name when it's the assistant's response
        model_used = role
        with st.chat_message("Assistant"):
            st.markdown(f"**{model_used}**")  # Display the model used for this response
            st.write(content)

# Prompt input field
prompt = st.chat_input("Ask anything")
if prompt:
    with st.chat_message("User"):
        st.write(prompt)
    st.session_state.chat_history.append((datetime.now(), "User", prompt))
    save_message(st.session_state.session_id, "User", prompt)  # Save user's message

    response_container = st.chat_message("Assistant")
    with response_container:
        st.markdown(f"**{selected_model}**")
        placeholder = st.empty()

    streamlit_callback = StreamlitCallbackHandler(placeholder)
    callback_manager = CallbackManager([streamlit_callback])

    clean_context = "\n".join(
        [f"{role}: {content}" for _, role, content in st.session_state.chat_history[-10:] if role == "User"]
    )
    full_input = f"{clean_context}\n{prompt}"

    llm = initialize_model(selected_model, callback_manager)
    if llm:
        messages = [HumanMessage(content=full_input)]
        llm.invoke(messages)

        full_response = streamlit_callback.get_full_response()

        # Store selected model name directly in the role field for assistant's message
        # Store assistant's response with model name in role
        st.session_state.chat_history.append((datetime.now(), selected_model, full_response))
        save_message(st.session_state.session_id, selected_model, full_response)  # Store model name in ROLE
  # Save model's response with model name in role
    else:
        st.error("Please select a valid model.")