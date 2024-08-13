import json
import time
import uuid

import boto3
from langchain.agents import AgentExecutor
from langchain_aws.agents.base import BedrockAgentsRunnable
from langchain_core.tools import tool

import operator
from typing import TypedDict, Annotated, Tuple
from typing import Union
from langchain_aws.agents.base import BedrockAgentAction, BedrockAgentFinish

from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt.tool_executor import ToolExecutor


def _create_iam_client():
    return boto3.client('iam')


def _create_agent_role(
        agent_region,
        foundational_model
) -> str:
    """
    Create agent resource role prior to creation of agent, at this point we do not have agentId, keep it as wildcard

    Args:
        agent_region: AWS region in which is the Agent if available
        foundational_model: The model used for inference in AWS BedrockAgents
    Returns:
       Agent execution role arn
    """
    try:
        account_id = boto3.client('sts').get_caller_identity().get('Account')
        assume_role_policy_document = json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "bedrock.amazonaws.com"
                    },
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "ArnLike": {
                            "aws:SourceArn": f"arn:aws:bedrock:{agent_region}:{account_id}:agent/*"
                        }
                    }
                }
            ]
        })
        managed_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AmazonBedrockAgentBedrockFoundationModelStatement",
                    "Effect": "Allow",
                    "Action": "bedrock:InvokeModel",
                    "Resource": [
                        f"arn:aws:bedrock:{agent_region}::foundation-model/{foundational_model}"
                    ]
                }
            ]
        }
        role_name = f'bedrock_agent_{uuid.uuid4()}'
        iam_client = _create_iam_client()
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=assume_role_policy_document,
            Description='Role for Bedrock Agent'
        )
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=f'AmazonBedrockAgentBedrockFoundationModelPolicy_{uuid.uuid4()}',
            PolicyDocument=json.dumps(managed_policy)
        )
        time.sleep(2)
        return response.get('Role', {}).get('Arn', '')

    except Exception as exception:
        raise exception


def _delete_agent_role(agent_resource_role_arn: str):
    """
    Delete agent resource role

    Args:
       agent_resource_role_arn: Associated Agent execution role arn
    """
    try:
        iam_client = _create_iam_client()
        role_name = agent_resource_role_arn.split('/')[-1]
        inline_policies = iam_client.list_role_policies(RoleName=role_name)
        for inline_policy_name in inline_policies.get('PolicyNames', []):
            iam_client.delete_role_policy(
                RoleName=role_name,
                PolicyName=inline_policy_name
            )
        iam_client.delete_role(
            RoleName=role_name
        )
    except Exception as exception:
        raise exception


def _delete_agent(agent_id):
    bedrock_client = boto3.client('bedrock-agent')
    bedrock_client.delete_agent(agentId=agent_id, skipResourceInUseCheck=True)


# --------------------------------------------------------------------------------------------------------#

@tool("AssetDetail::getAssetValue")
def getAssetValue(asset_holder_id: str) -> str:
    """Get the asset value for an owner id"""
    return f"The total asset value for {asset_holder_id} is 100K"


@tool("AssetDetail::getMortgageRate")
def getMortgageRate(asset_holder_id: str, asset_value: str) -> str:
    """Get the mortgage rate based on asset value"""
    return (
        f"The mortgage rate for {asset_holder_id} "
        f"with asset value of {asset_value} is 8.87%"
    )


def test_mortgage_bedrock_agent() -> None:
    foundational_model = 'anthropic.claude-3-sonnet-20240229-v1:0'
    tools = [getAssetValue, getMortgageRate]
    agent_resource_role_arn = None
    agent = None
    try:
        agent_resource_role_arn = _create_agent_role(
            agent_region='us-west-2',
            foundational_model=foundational_model)
        agent = BedrockAgentsRunnable.create_agent(
            agent_name="mortgage_interest_rate_agent",
            agent_resource_role_arn=agent_resource_role_arn,
            model=foundational_model,
            instructions="""
            You are an agent who helps with getting the mortgage rate based on the current asset valuation""",
            tools=tools,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools)  # type: ignore[arg-type]
        output = agent_executor.invoke(
            {"input": "what is my mortgage rate for id AVC-1234"}
        )

        assert output["output"] == ("The mortgage rate for the asset holder id AVC-1234 "
                                    "with an asset value of 100K is 8.87%.")
    except Exception as ex:
        raise ex
    finally:
        if agent_resource_role_arn:
            _delete_agent_role(agent_resource_role_arn)
        if agent:
            _delete_agent(agent.agent_id)


# --------------------------------------------------------------------------------------------------------#
@tool
def getWeather(location: str = '') -> str:
    """
        Get the weather of a location

        Args:
            location: location of the place
    """
    if location.lower() == 'seattle':
        return f"It is raining in {location}"
    return f"It is hot and humid in {location}"


def test_weather_agent():
    foundational_model = 'anthropic.claude-3-sonnet-20240229-v1:0'
    tools = [getWeather]
    agent_resource_role_arn = None
    agent = None
    try:
        agent_resource_role_arn = _create_agent_role(
            agent_region='us-west-2',
            foundational_model=foundational_model)
        agent = BedrockAgentsRunnable.create_agent(
            agent_name="weather_agent",
            agent_resource_role_arn=agent_resource_role_arn,
            model=foundational_model,
            instructions="""
                You are an agent who helps with getting weather for a given location""",
            tools=tools
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools)  # type: ignore[arg-type]
        output = agent_executor.invoke(
            {"input": "what is the weather in Seattle?"}
        )

        assert output["output"] == "It is raining in Seattle"
    except Exception as ex:
        raise ex
    finally:
        if agent_resource_role_arn:
            _delete_agent_role(agent_resource_role_arn)
        if agent:
            _delete_agent(agent.agent_id)


# # --------------------------------------------------------------------------------------------------------#

@tool("AssetDetail::getAssetValue")
def getTotalAssetValue(asset_holder_id: str = '') -> str:
    """
        Get the asset value for an owner id

        Args:
            asset_holder_id: id of the owner holding the asset
        Returns:
            str -> the valuation of the asset
        """
    return f"The total asset value for {asset_holder_id} is 100K"


@tool("MortgateEvaluation::getMortgateEvaluation")
def getMortgateEvaluation(asset_holder_id: str = '', asset_value: int = 0) -> str:
    """
        Get the mortgage rate based on asset value

        Args:
            asset_holder_id: id of the owner holding the asset
            asset_value: asset value which is used to get the mortgage rate
        Returns:
            str -> the calculated mortgage rate based on the asset value
        """
    return f"The mortgage rate for {asset_holder_id} with asset value of {asset_value} is 8.87%"


def test_multi_serial_actions_agent():
    foundational_model = 'anthropic.claude-3-sonnet-20240229-v1:0'
    tools = [getTotalAssetValue, getMortgateEvaluation]
    agent_resource_role_arn = None
    agent = None
    try:
        agent_resource_role_arn = _create_agent_role(
            agent_region='us-west-2',
            foundational_model=foundational_model)
        agent = BedrockAgentsRunnable.create_agent(
            agent_name="weather_agent",
            agent_resource_role_arn=agent_resource_role_arn,
            model=foundational_model,
            instructions="""
                    You are an agent who helps with getting weather for a given location""",
            tools=tools
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools)  # type: ignore[arg-type]
        output = agent_executor.invoke(
            {"input": "what is my mortgage rate for id AVC-1234?"}
        )

        assert output["output"] == "The mortgage rate for the asset holder id AVC-1234 is 8.87%"
    except Exception as ex:
        raise ex
    finally:
        if agent_resource_role_arn:
            _delete_agent_role(agent_resource_role_arn)
        if agent:
            _delete_agent(agent.agent_id)


# # --------------------------------------------------------------------------------------------------------#
def should_continue(data):
    # If the agent outcome is an AgentFinish, then we return `exit` string
    # This will be used when setting up the graph to define the flow
    output_ = data["output"]
    if isinstance(output_, list) and len(output_) > 0 and isinstance(output_[0], BedrockAgentAction):
        return "continue"

    if isinstance(output_, BedrockAgentFinish):
        return "end"
    # Otherwise, an AgentAction is returned
    # Here we return `continue` string
    # This will be used when setting up the graph to define the flow
    else:
        return "continue"


tool_executor = ToolExecutor([getWeather])


# Define the function to execute tools
def execute_tools(data):
    # Get the most recent output - this is the key added in the `agent` above
    agent_action = data["output"]
    output = tool_executor.invoke(agent_action[0])
    tuple_output = agent_action[0], output
    return {"intermediate_steps": [tuple_output]}


def get_weather_agent_node() -> Tuple[BedrockAgentsRunnable, str]:
    foundational_model = 'anthropic.claude-3-sonnet-20240229-v1:0'
    tools = [getWeather]
    try:
        agent_resource_role_arn = _create_agent_role(
            agent_region='us-west-2',
            foundational_model=foundational_model)
        agent = BedrockAgentsRunnable.create_agent(
            agent_name="weather_agent",
            agent_resource_role_arn=agent_resource_role_arn,
            model=foundational_model,
            instructions="""
                    You are an agent who helps with getting weather for a given location""",
            tools=tools
        )

        return agent, agent_resource_role_arn
    except Exception as e:
        raise e


agent_runnable, agent_resource_role_arn = get_weather_agent_node()


def run_agent(data):
    agent_outcome = agent_runnable.invoke(data)
    return {"output": agent_outcome}


def test_bedrock_agent_lang_graph():
    # Define a new graph
    workflow = StateGraph(AgentState)

    # Define the two nodes we will cycle between
    workflow.add_node("agent", run_agent)
    workflow.add_node("action", execute_tools)

    # Set the entrypoint as `agent`
    # This means that this node is the first one called
    workflow.add_edge(START, "agent")

    # We now add a conditional edge
    workflow.add_conditional_edges(
        # First, we define the start node. We use `agent`.
        # This means these are the edges taken after the `agent` node is called.
        "agent",
        # Next, we pass in the function that will determine which node is called next.
        should_continue,
        # Finally we pass in a mapping.
        # The keys are strings, and the values are other nodes.
        # END is a special node marking that the graph should finish.
        # What will happen is we will call `should_continue`, and then the output of that
        # will be matched against the keys in this mapping.
        # Based on which one it matches, that node will then be called.
        {
            # If `tools`, then we call the tool node.
            "continue": "action",
            # Otherwise we finish.
            "end": END,
        },
    )

    # We now add a normal edge from `tools` to `agent`.
    # This means that after `tools` is called, `agent` node is called next.
    workflow.add_edge("action", "agent")

    # Finally, we compile it!
    # This compiles it into a LangChain Runnable,
    # meaning you can use it as you would any other runnable
    app = workflow.compile()

    inputs = {"input": "what is the weather in seattle?"}
    final_state = app.invoke(inputs)

    assert isinstance(final_state.get('output', {}), BedrockAgentFinish)
    assert final_state.get('output').return_values['output'] == 'It is raining in Seattle'


class AgentState(TypedDict):
    input: str
    output: Union[BedrockAgentAction, BedrockAgentFinish, None]
    intermediate_steps: Annotated[list[tuple[BedrockAgentAction, str]], operator.add]
