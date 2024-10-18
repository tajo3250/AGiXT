import importlib
import os
import glob
from inspect import signature, Parameter
import logging
import inspect
from Globals import getenv, DEFAULT_USER
from MagicalAuth import get_user_id
from agixtsdk import AGiXTSDK
from DB import (
    get_session,
    Chain as ChainDB,
    User,
)

logging.basicConfig(
    level=getenv("LOG_LEVEL"),
    format=getenv("LOG_FORMAT"),
)
DISABLED_EXTENSIONS = getenv("DISABLED_EXTENSIONS").replace(" ", "").split(",")


class Extensions:
    def __init__(
        self,
        agent_name="",
        agent_id=None,
        agent_config=None,
        conversation_name="",
        conversation_id=None,
        ApiClient=None,
        api_key=None,
        user=DEFAULT_USER,
    ):
        self.agent_config = agent_config
        self.agent_name = agent_name if agent_name else "gpt4free"
        self.conversation_name = conversation_name
        self.conversation_id = conversation_id
        self.agent_id = agent_id
        self.ApiClient = (
            ApiClient
            if ApiClient
            else AGiXTSDK(base_uri=getenv("API_URL"), api_key=api_key)
        )
        self.api_key = api_key
        self.user = user
        self.user_id = get_user_id(self.user)
        self.commands = self.load_commands()
        if agent_config != None:
            if "commands" not in self.agent_config:
                self.agent_config["commands"] = {}
            if self.agent_config["commands"] == None:
                self.agent_config["commands"] = {}
            self.available_commands = self.get_available_commands()
        else:
            self.agent_config = {
                "settings": {},
                "commands": {},
            }

    async def execute_chain(self, chain_name, user_input="", **kwargs):
        return await self.ApiClient.run_chain(
            chain_name=chain_name, user_input=user_input, chain_args=kwargs
        )

    def get_available_commands(self):
        if self.commands == []:
            return []
        available_commands = []
        for command in self.commands:
            friendly_name, command_module, command_name, command_args = command
            if (
                "commands" in self.agent_config
                and friendly_name in self.agent_config["commands"]
            ):
                if str(self.agent_config["commands"][friendly_name]).lower() == "true":
                    available_commands.append(
                        {
                            "friendly_name": friendly_name,
                            "name": command_name,
                            "args": command_args,
                            "enabled": True,
                        }
                    )
        return available_commands

    def get_enabled_commands(self):
        enabled_commands = []
        for command in self.available_commands:
            if command["enabled"]:
                enabled_commands.append(command)
        return enabled_commands

    def get_command_args(self, command_name: str):
        extensions = self.get_extensions()
        for extension in extensions:
            for command in extension["commands"]:
                if command["friendly_name"] == command_name:
                    return command["command_args"]
        return {}

    def get_chains(self):
        session = get_session()
        user_data = session.query(User).filter(User.email == DEFAULT_USER).first()
        global_chains = (
            session.query(ChainDB).filter(ChainDB.user_id == user_data.id).all()
        )
        chains = session.query(ChainDB).filter(ChainDB.user_id == self.user_id).all()
        chain_list = []
        for chain in chains:
            chain_list.append(chain.name)
        for chain in global_chains:
            chain_list.append(chain.name)
        session.close()
        return chain_list

    def get_chain_args(self, chain_name):
        skip_args = [
            "command_list",
            "context",
            "COMMANDS",
            "date",
            "conversation_history",
            "agent_name",
            "working_directory",
            "helper_agent_name",
        ]
        chain_data = self.get_chain(chain_name=chain_name)
        steps = chain_data["steps"]
        prompt_args = []
        args = []
        for step in steps:
            try:
                prompt = step["prompt"]
                prompt_category = (
                    prompt["category"] if "category" in prompt else "Default"
                )
                if "prompt_name" in prompt:
                    args = self.ApiClient.get_prompt_args(
                        prompt_name=prompt["prompt_name"],
                        prompt_category=prompt_category,
                    )
                elif "command_name" in prompt:
                    args = Extensions().get_command_args(
                        command_name=prompt["command_name"]
                    )
                elif "chain_name" in prompt:
                    args = self.get_chain_args(chain_name=prompt["chain_name"])
                for arg in args:
                    if arg not in prompt_args and arg not in skip_args:
                        prompt_args.append(arg)
            except Exception as e:
                logging.error(f"Error getting chain args: {e}")
        return prompt_args

    def load_commands(self):
        try:
            settings = self.agent_config["settings"]
        except:
            settings = {}
        commands = []
        command_files = glob.glob("extensions/*.py")
        for command_file in command_files:
            module_name = os.path.splitext(os.path.basename(command_file))[0]
            if module_name in DISABLED_EXTENSIONS:
                continue
            module = importlib.import_module(f"extensions.{module_name}")
            if issubclass(getattr(module, module_name), Extensions):
                command_class = getattr(module, module_name)(**settings)
                if hasattr(command_class, "commands"):
                    for (
                        command_name,
                        command_function,
                    ) in command_class.commands.items():
                        params = self.get_command_params(command_function)
                        commands.append(
                            (
                                command_name,
                                getattr(module, module_name),
                                command_function.__name__,
                                params,
                            )
                        )
        chains = self.get_chains()
        for chain in chains:
            chain_args = self.get_chain_args(chain)
            commands.append(
                (
                    chain,
                    self.execute_chain,
                    "run_chain",
                    {
                        "chain_name": chain,
                        "user_input": "",
                        **{arg: "" for arg in chain_args},
                    },
                )
            )
        return commands

    def get_extension_settings(self):
        settings = {}
        command_files = glob.glob("extensions/*.py")
        for command_file in command_files:
            module_name = os.path.splitext(os.path.basename(command_file))[0]
            if module_name in DISABLED_EXTENSIONS:
                continue
            module = importlib.import_module(f"extensions.{module_name}")
            if issubclass(getattr(module, module_name), Extensions):
                command_class = getattr(module, module_name)()
                params = self.get_command_params(command_class.__init__)
                # Remove self and kwargs from params
                if "self" in params:
                    del params["self"]
                if "kwargs" in params:
                    del params["kwargs"]
                if params != {}:
                    settings[module_name] = params

        # Add settings for chains
        chains = self.get_chains()
        for chain in chains:
            chain_args = self.get_chain_args(chain)
            if chain_args:
                settings[f"chain_{chain}"] = {
                    "chain_name": chain,
                    "user_input": "",
                    **{arg: "" for arg in chain_args},
                }

        return settings

    def find_command(self, command_name: str):
        for name, module, function_name, params in self.commands:
            if module.__name__ in DISABLED_EXTENSIONS:
                continue
            if name == command_name:
                if isinstance(module, type):  # It's a class
                    command_function = getattr(module, function_name)
                    return command_function, module, params
                else:  # It's a function (for chains)
                    return module, None, params
        return None, None, None

    def get_commands_list(self):
        self.commands = self.load_commands()
        commands_list = [command_name for command_name, _, _ in self.commands]
        return commands_list

    async def execute_command(self, command_name: str, command_args: dict = None):
        injection_variables = {
            "user": self.user,
            "agent_name": self.agent_name,
            "command_name": command_name,
            "conversation_name": self.conversation_name,
            "conversation_id": self.conversation_id,
            "agent_id": self.agent_id,
            "enabled_commands": self.get_enabled_commands(),
            "ApiClient": self.ApiClient,
            "api_key": self.api_key,
            "conversation_directory": os.path.join(
                os.getcwd(), "WORKSPACE", self.agent_id, self.conversation_id
            ),
            **self.agent_config["settings"],
        }
        command_function, module, params = self.find_command(command_name=command_name)
        logging.info(
            f"Executing command: {command_name} with args: {command_args}. Command Function: {command_function}"
        )
        if command_function is None:
            logging.error(f"Command {command_name} not found")
            return f"Command {command_name} not found"

        if command_args is None:
            command_args = {}

        for param in params:
            if param not in command_args:
                if param != "self" and param != "kwargs":
                    command_args[param] = None
        args = command_args.copy()
        for param in command_args:
            if param not in params:
                del args[param]

        if module is None:  # It's a chain
            return await command_function(
                chain_name=command_name, user_input="", **args
            )
        else:  # It's a regular command
            return await getattr(
                module(
                    **injection_variables,
                ),
                command_function.__name__,
            )(**args)

    def get_command_params(self, func):
        params = {}
        sig = signature(func)
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            if param.default == Parameter.empty:
                params[name] = ""
            else:
                params[name] = param.default
        return params

    def get_extensions(self):
        commands = []
        command_files = glob.glob("extensions/*.py")
        for command_file in command_files:
            module_name = os.path.splitext(os.path.basename(command_file))[0]
            if module_name in DISABLED_EXTENSIONS:
                continue
            module = importlib.import_module(f"extensions.{module_name}")
            command_class = getattr(module, module_name.lower())()
            extension_name = command_file.split("/")[-1].split(".")[0]
            extension_name = extension_name.replace("_", " ").title()
            try:
                extension_description = inspect.getdoc(command_class)
            except:
                extension_description = extension_name
            constructor = inspect.signature(command_class.__init__)
            params = constructor.parameters
            extension_settings = [
                name for name in params if name != "self" and name != "kwargs"
            ]
            extension_commands = []
            if hasattr(command_class, "commands"):
                try:
                    for (
                        command_name,
                        command_function,
                    ) in command_class.commands.items():
                        params = self.get_command_params(command_function)
                        try:
                            command_description = inspect.getdoc(command_function)
                        except:
                            command_description = command_name
                        extension_commands.append(
                            {
                                "friendly_name": command_name,
                                "description": command_description,
                                "command_name": command_function.__name__,
                                "command_args": params,
                            }
                        )
                except Exception as e:
                    logging.error(f"Error getting commands: {e}")
            commands.append(
                {
                    "extension_name": extension_name,
                    "description": extension_description,
                    "settings": extension_settings,
                    "commands": extension_commands,
                }
            )
        return commands
