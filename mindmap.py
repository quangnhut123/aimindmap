from __future__ import annotations
import hmac
import re
from typing import Optional, Tuple, List, Union, Literal
import base64
import matplotlib.pyplot as plt
import networkx as nx
import streamlit as st
from streamlit.delta_generator import DeltaGenerator
import os
from openai import OpenAI
from graphviz import Digraph, Graph
from dataclasses import dataclass
from textwrap import dedent
from streamlit_agraph import agraph, Node, Edge, Config
# set title of page (will be seen in tab) and the width
st.set_page_config(page_title="AI Maps Generator", page_icon=None, layout="wide")
# Remove Streamlit footer
hide_streamlit_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            </style>
            """
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

COLOR = "orange"
FOCUS_COLOR = "green"
OPENAI_MODEL = "gpt-4"

client = OpenAI(
  api_key = st.secrets["openai_api_key"],
)


@dataclass
class Message:
    """A class that represents a message in a ChatGPT conversation.
    """
    content: str
    role: Literal["user", "system", "assistant"]

    # is a built-in method for dataclasses
    # called after the __init__ method
    def __post_init__(self):
        self.content = dedent(self.content).strip()

START_CONVERSATION = [
    Message("""
        You are a useful mind map/undirected graph-generating AI that can generate mind maps
        based on any input or instructions.
    """, role="system"),
    Message("""
        You have the ability to perform the following actions given a request
        to construct or modify a mind map/graph:

        1. add(node1, node2) - add an edge between node1 and node2
        2. delete(node1, node2) - delete the edge between node1 and node2
        3. delete(node1) - deletes every edge connected to node1

        Note that the graph is undirected and thus the order of the nodes does not matter
        and duplicates will be ignored. Another important note: the graph should be sparse,
        with many nodes and few edges from each node. Too many edges will make it difficult
        to understand and hard to read. The answer should only include the actions to perform,
        nothing else. If the instructions are vague or even if only a single word is provided,
        still generate a graph of multiple nodes and edges that that could makes sense in the
        situation. Remember to think step by step and debate pros and cons before settling on
        an answer to accomplish the request as well as possible.

        Here is my first request: Add a mind map about machine learning.
    """, role="user"),
    Message("""
        add("Machine learning","AI")
        add("Machine learning", "Reinforcement learning")
        add("Machine learning", "Supervised learning")
        add("Machine learning", "Unsupervised learning")
        add("Supervised learning", "Regression")
        add("Supervised learning", "Classification")
        add("Unsupervised learning", "Clustering")
        add("Unsupervised learning", "Anomaly Detection")
        add("Unsupervised learning", "Dimensionality Reduction")
        add("Unsupervised learning", "Association Rule Learning")
        add("Clustering", "K-means")
        add("Classification", "Logistic Regression")
        add("Reinforcement learning", "Proximal Policy Optimization")
        add("Reinforcement learning", "Q-learning")
    """, role="assistant"),
    Message("""
        Remove the parts about reinforcement learning and K-means.
    """, role="user"),
    Message("""
        delete("Reinforcement learning")
        delete("Clustering", "K-means")
    """, role="assistant")
]

def ask_chatgpt(conversation: List[Message]) -> Tuple[str, List[Message]]:
    response = client.chat.completions.create(
        model = OPENAI_MODEL,
        temperature = 0,
        messages=[{"role": c.role, "content": c.content} for c in conversation]
    )
    try:
        # Attempting to access attributes directly
        latest_message = response.choices[0].message
    except AttributeError:
        # Fallback or adjust based on the actual API/client library structure
        print("Unexpected response structure:", response)
        raise

    # Create a new Message instance for the generated response
    msg = Message(content=latest_message.content, role=latest_message.role)

    # return the text output and the new conversation
    return msg.content, conversation + [msg]

def ask_gpt_for_roadmap(query: str):
    prompt = f"""
    Based on the goal of '{query}', list up to 10 main steps in simplified form and not numbering format, suitable for use as titles in a flowchart. Focus on clear and concise titles for each step.
    """
    response = client.chat.completions.create(
        model = OPENAI_MODEL,
        temperature = 0,
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Answer in same language of goal"},
            {"role": "user", "content": prompt}
        ]
    )
    roadmap_text = response.choices[0].message.content
    return roadmap_text

def visualize_roadmap_as_flowchart(roadmap: str):
    """Visualize the roadmap as a flowchart using Graphviz."""
    steps = [step.strip() for step in roadmap.split('\n') if step.strip()]

    dot = Digraph(comment='Roadmap')
    dot.attr('node', style='filled', fillcolor='orange', fontcolor='black', shape='box')
    dot.attr('edge', color='grey')

    for i, step in enumerate(steps):
        step = step.replace("-", "").replace('"', '').replace('.', '')
        dot.node(str(i), step)
        if i > 0:
            dot.edge(str(i-1), str(i))

    b64 = base64.b64encode(dot.pipe(format='svg')).decode("utf-8")
    html = f"<img style='width: 100%' src='data:image/svg+xml;base64,{b64}'/>"
    st.write(html, unsafe_allow_html=True)

class MindMap:
    """A class that represents a mind map as a graph.
    """

    def __init__(self, edges: Optional[List[Tuple[str, str]]]=None, nodes: Optional[List[str]]=None) -> None:
        self.edges = [] if edges is None else edges
        self.nodes = [] if nodes is None else nodes
        self.save()

    @classmethod
    def load(cls) -> MindMap:
        """Load mindmap from session state if it exists

        Returns: Mindmap
        """
        if "mindmap" in st.session_state:
            return st.session_state["mindmap"]
        return cls()

    def save(self) -> None:
        # save to session state
        st.session_state["mindmap"] = self

    def is_empty(self) -> bool:
        return len(self.edges) == 0

    def ask_for_initial_graph(self, query: str) -> None:
        """Ask GPT-4 to construct a graph from scrach.

        Args:
            query (str): The query to ask GPT-4 about.

        Returns:
            str: The output from GPT-4.
        """

        conversation = START_CONVERSATION + [
            Message(f"""
                Great, now ignore all previous nodes and restart from scratch. I now want you do the following:

                {query}
            """, role="user")
        ]

        output, self.conversation = ask_chatgpt(conversation)
        # replace=True to restart
        self.parse_and_include_edges(output, replace=True)

    def ask_for_extended_graph(self, selected_node: Optional[str]=None, text: Optional[str]=None) -> None:
        """Cached helper function to ask GPT-4 to extend the graph.

        Args:
            query (str): query to ask GPT-4 about
            edges_as_text (str): edges formatted as text

        Returns:
            str: GPT-4 output
        """

        # do nothing
        if (selected_node is None and text is None):
            return

        # change description depending on if a node
        # was selected or a text description was given
        #
        # note that the conversation is copied (shallowly) instead
        # of modified in place. The reason for this is that if
        # the chatgpt call fails self.conversation will not
        # be updated
        if selected_node is not None:
            # prepend a description that this node
            # should be extended
            conversation = self.conversation + [
                Message(f"""
                    add new edges to new nodes, starting from the node "{selected_node}"
                """, role="user")
            ]
            st.session_state.last_expanded = selected_node
        else:
            # just provide the description
            conversation = self.conversation + [Message(text, role="user")]

        # now self.conversation is updated
        output, self.conversation = ask_chatgpt(conversation)
        self.parse_and_include_edges(output, replace=False)

    def parse_and_include_edges(self, output: str, replace: bool=True) -> None:
        """Parse output from LLM (GPT-4) and include the edges in the graph.

        Args:
            output (str): output from LLM (GPT-4) to be parsed
            replace (bool, optional): if True, replace all edges with the new ones,
                otherwise add to existing edges. Defaults to True.
        """

        # Regex patterns
        pattern1 = r'(add|delete)\("([^()"]+)",\s*"([^()"]+)"\)'
        pattern2 = r'(delete)\("([^()"]+)"\)'

        # Find all matches in the text
        matches = re.findall(pattern1, output) + re.findall(pattern2, output)

        new_edges = []
        remove_edges = set()
        remove_nodes = set()
        for match in matches:
            op, *args = match
            add = op == "add"
            if add or (op == "delete" and len(args)==2):
                a, b = args
                if a == b:
                    continue
                if add:
                    new_edges.append((a, b))
                else:
                    # remove both directions
                    # (undirected graph)
                    remove_edges.add(frozenset([a, b]))
            else: # must be delete of node
                remove_nodes.add(args[0])

        if replace:
            edges = new_edges
        else:
            edges = self.edges + new_edges

        # make sure edges aren't added twice
        # and remove nodes/edges that were deleted
        added = set()
        for edge in edges:
            nodes = frozenset(edge)
            if nodes in added or nodes & remove_nodes or nodes in remove_edges:
                continue
            added.add(nodes)

        self.edges = list([tuple(a) for a in added])
        self.nodes = list(set([n for e in self.edges for n in e]))
        self.save()

    def _delete_node(self, node) -> None:
        """Delete a node and all edges connected to it.

        Args:
            node (str): The node to delete.
        """
        self.edges = [e for e in self.edges if node not in frozenset(e)]
        self.nodes = list(set([n for e in self.edges for n in e]))
        self.conversation.append(Message(
            f'delete("{node}")',
            role="user"
        ))
        self.save()

    def _add_expand_delete_buttons(self, node) -> None:
        st.sidebar.subheader(node)
        cols = st.sidebar.columns(2)
        cols[0].button(
            label="Expand",
            on_click=self.ask_for_extended_graph,
            key=f"expand_{node}",
            # pass to on_click (self.ask_for_extended_graph)
            kwargs={"selected_node": node}
        )
        cols[1].button(
            label="Delete",
            on_click=self._delete_node,
            type="primary",
            key=f"delete_{node}",
            # pass on to _delete_node
            args=(node,)
        )

    def visualize(self, graph_type: Literal["agraph", "networkx", "graphviz"]) -> None:
        """Visualize the mindmap as a graph a certain way depending on the `graph_type`.

        Args:
            graph_type (Literal["agraph", "networkx", "graphviz"]): The graph type to visualize the mindmap as.
        Returns:
            Union[str, None]: Any output from the clicking the graph or
                if selecting a node in the sidebar.
        """

        selected = st.session_state.get("last_expanded")
        if graph_type == "agraph":
            vis_nodes = [
                Node(
                    id=n,
                    label=n,
                    # a little bit bigger if selected
                    size=10+10*(n==selected),
                    # a different color if selected
                    color=COLOR if n != selected else FOCUS_COLOR
                )
                for n in self.nodes
            ]
            vis_edges = [Edge(source=a, target=b) for a, b in self.edges]
            config = Config(width="100%",
                            height=600,
                            directed=False,
                            physics=True,
                            hierarchical=False,
                            )
            # returns a node if clicked, otherwise None
            clicked_node = agraph(nodes=vis_nodes,
                            edges=vis_edges,
                            config=config)
            # if clicked, update the sidebar with a button to create it
            if clicked_node is not None:
                self._add_expand_delete_buttons(clicked_node)
            return
        if graph_type == "networkx":
            graph = nx.Graph()
            for a, b in self.edges:
                graph.add_edge(a, b)
            colors = [FOCUS_COLOR if node == selected else COLOR for node in graph]
            fig, _ = plt.subplots(figsize=(16, 16))
            pos = nx.spring_layout(graph, seed = 123)
            nx.draw(graph, pos=pos, node_color=colors, with_labels=True)
            st.pyplot(fig)
        else: # graph_type == "graphviz":
            graph = Graph()
            graph.attr(rankdir='TB')
            for a, b in self.edges:
                graph.edge(a, b, dir="both")
            for n in self.nodes:
                graph.node(n, style="filled", fillcolor=FOCUS_COLOR if n == selected else COLOR)
            # st.graphviz_chart(graph, use_container_width=True)
            b64 = base64.b64encode(graph.pipe(format='svg')).decode("utf-8")
            html = f"<img style='width: 100%' src='data:image/svg+xml;base64,{b64}'/>"
            st.write(html, unsafe_allow_html=True)
        # sort alphabetically
        for node in sorted(self.nodes):
            self._add_expand_delete_buttons(node)


def main():
    # will initialize the graph from session state
    # (if it exists) otherwise will create a new one
    mindmap = MindMap.load()

    st.sidebar.title("AI Maps Generator")

    graph_type = st.sidebar.radio("Type of graph", options=["agraph", "networkx", "graphviz", "roadmap"])

    empty = mindmap.is_empty()
    reset = empty or st.sidebar.checkbox("Reset mind map", value=False)
    query = st.sidebar.text_area(
        "Describe your map" if reset else "Describe how to change your mind map",
        value=st.session_state.get("mindmap-input", ""),
        key="mindmap-input",
        height=200
    )
    submit = st.sidebar.button("Generate")

    valid_submission = submit and query != ""

    if empty and not valid_submission:
        return

    with st.spinner(text="Generating graph..."):
        # if submit and non-empty query, then update graph
        if valid_submission:
            if graph_type == "roadmap":
                roadmap_text = ask_gpt_for_roadmap(query)
                if roadmap_text:
                    visualize_roadmap_as_flowchart(roadmap_text)
            else:
                if reset:
                    # completely new mindmap
                    mindmap.ask_for_initial_graph(query=query)
                else:
                    # extend existing mindmap
                    mindmap.ask_for_extended_graph(text=query)
                # since inputs also have to be updated, everything
                # is rerun
                st.rerun()
        else:
            mindmap.visualize(graph_type)


def check_password():
    """Returns `True` if the user had the correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if hmac.compare_digest(st.session_state["password"], st.secrets["password"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store the password.
        else:
            st.session_state["password_correct"] = False

    # Return True if the password is validated.
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password.
    st.text_input(
        "Password", type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in st.session_state:
        st.error("😕 Password incorrect")
    return False


if __name__ == "__main__":
    # main()
    if not check_password():
        st.stop()
    else:
        main()
