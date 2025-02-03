"""
See our docs for details about our datamodel: https://kiln-ai.github.io/Kiln/kiln_core_docs/kiln_ai.html
"""

from __future__ import annotations

import json
import math
import random
from enum import Enum, IntEnum
from typing import TYPE_CHECKING, Callable, Dict, List, Type, Union

import jsonschema
import jsonschema.exceptions
from pydantic import (
    BaseModel,
    Field,
    ValidationInfo,
    model_validator,
)
from typing_extensions import Self

from kiln_ai.datamodel.json_schema import JsonObjectSchema, schema_from_json_str

from .basemodel import (
    ID_FIELD,
    ID_TYPE,
    NAME_FIELD,
    SHORT_NAME_FIELD,
    KilnBaseModel,
    KilnParentedModel,
    KilnParentModel,
)
from .json_schema import validate_schema

if TYPE_CHECKING:
    from . import Task


__all__ = [
    "basemodel",
    "json_schema",
    "Task",
    "Project",
    "TaskRun",
    "TaskOutput",
    "TaskOutputRating",
    "Priority",
    "DataSource",
    "DataSourceType",
    "DataSourceProperty",
    "Finetune",
    "FineTuneStatusType",
    "TaskOutputRatingType",
    "TaskRequirement",
    "TaskDeterminism",
    "DatasetSplitDefinition",
    "DatasetSplit",
    "RequirementRating",
    "TaskRequirement",
    "strict_mode",
    "set_strict_mode",
    "Prompt",
]


# We want to be hard on ourselves for data completeness generated by the Kiln App, but don't want to make it hard for users to use the datamodel/library.
# Strict mode enables extra validations that we want to enforce in Kiln App (and any other client that wants best practices), but not in the library (unless they opt in)
_strict_mode: bool = False


def strict_mode() -> bool:
    return _strict_mode


def set_strict_mode(value: bool) -> None:
    global _strict_mode
    _strict_mode = value


class Priority(IntEnum):
    """Defines priority levels for tasks and requirements, where P0 is highest priority."""

    p0 = 0
    p1 = 1
    p2 = 2
    p3 = 3


# Only one rating type for now, but this allows for extensibility if we want to add more in the future
class TaskOutputRatingType(str, Enum):
    """Defines the types of rating systems available for task outputs."""

    five_star = "five_star"
    pass_fail = "pass_fail"
    pass_fail_critical = "pass_fail_critical"
    custom = "custom"


class RequirementRating(BaseModel):
    """Rating for a specific requirement within a task output."""

    value: float = Field(
        description="The rating value. Interpretation depends on rating type"
    )
    type: TaskOutputRatingType = Field(description="The type of rating")


class TaskOutputRating(KilnBaseModel):
    """
    A rating for a task output, including an overall rating and ratings for each requirement.

    Supports:
    - five_star: 1-5 star ratings
    - pass_fail: boolean pass/fail (1.0 = pass, 0.0 = fail)
    - pass_fail_critical: tri-state (1.0 = pass, 0.0 = fail, -1.0 = critical fail)
    """

    type: TaskOutputRatingType = Field(default=TaskOutputRatingType.five_star)
    value: float | None = Field(
        description="The rating value. Interpretation depends on rating type:\n- five_star: 1-5 stars\n- pass_fail: 1.0 (pass) or 0.0 (fail)\n- pass_fail_critical: 1.0 (pass), 0.0 (fail), or -1.0 (critical fail)",
        default=None,
    )
    requirement_ratings: Dict[ID_TYPE, RequirementRating] = Field(
        default={},
        description="The ratings of the requirements of the task.",
    )

    # Previously we stored rating values as a dict of floats, but now we store them as RequirementRating objects.
    @model_validator(mode="before")
    def upgrade_old_format(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data

        # Check if we have the old format (dict of floats)
        req_ratings = data.get("requirement_ratings", {})
        if req_ratings and all(
            isinstance(v, (int, float)) for v in req_ratings.values()
        ):
            # Convert each float to a RequirementRating object
            # all ratings are five star at the point we used this format
            data["requirement_ratings"] = {
                k: {"value": v, "type": TaskOutputRatingType.five_star}
                for k, v in req_ratings.items()
            }

        return data

    # Used to select high quality outputs for example selection (MultiShotPromptBuilder, etc)
    def is_high_quality(self) -> bool:
        if self.value is None:
            return False

        if self.type == TaskOutputRatingType.five_star:
            return self.value >= 4
        elif self.type == TaskOutputRatingType.pass_fail:
            return self.value == 1.0
        elif self.type == TaskOutputRatingType.pass_fail_critical:
            return self.value == 1.0
        return False

    @model_validator(mode="after")
    def validate_rating(self) -> Self:
        if self.type not in TaskOutputRatingType:
            raise ValueError(f"Invalid rating type: {self.type}")

        # Overall rating is optional
        if self.value is not None:
            self._validate_rating(self.type, self.value, "overall rating")

        for req_id, req_rating in self.requirement_ratings.items():
            self._validate_rating(
                req_rating.type,
                req_rating.value,
                f"requirement rating for req ID: {req_id}",
            )

        return self

    def _validate_rating(
        self, type: TaskOutputRatingType, rating: float | None, rating_name: str
    ) -> None:
        if type == TaskOutputRatingType.five_star:
            self._validate_five_star(rating, rating_name)
        elif type == TaskOutputRatingType.pass_fail:
            self._validate_pass_fail(rating, rating_name)
        elif type == TaskOutputRatingType.pass_fail_critical:
            self._validate_pass_fail_critical(rating, rating_name)

    def _validate_five_star(self, rating: float | None, rating_name: str) -> None:
        if rating is None or not isinstance(rating, float) or not rating.is_integer():
            raise ValueError(
                f"{rating_name.capitalize()} of type five_star must be an integer value (1-5)"
            )
        if rating < 1 or rating > 5:
            raise ValueError(
                f"{rating_name.capitalize()} of type five_star must be between 1 and 5 stars"
            )

    def _validate_pass_fail(self, rating: float | None, rating_name: str) -> None:
        if rating is None or not isinstance(rating, float) or not rating.is_integer():
            raise ValueError(
                f"{rating_name.capitalize()} of type pass_fail must be an integer value (0 or 1)"
            )
        if rating not in [0, 1]:
            raise ValueError(
                f"{rating_name.capitalize()} of type pass_fail must be 0 (fail) or 1 (pass)"
            )

    def _validate_pass_fail_critical(
        self, rating: float | None, rating_name: str
    ) -> None:
        if rating is None or not isinstance(rating, float) or not rating.is_integer():
            raise ValueError(
                f"{rating_name.capitalize()} of type pass_fail_critical must be an integer value (-1, 0, or 1)"
            )
        if rating not in [-1, 0, 1]:
            raise ValueError(
                f"{rating_name.capitalize()} of type pass_fail_critical must be -1 (critical fail), 0 (fail), or 1 (pass)"
            )


class TaskOutput(KilnBaseModel):
    """
    An output for a specific task run.

    Contains the actual output content, its source (human or synthetic),
    and optional rating information.
    """

    output: str = Field(
        description="The output of the task. JSON formatted for structured output, plaintext for unstructured output."
    )
    source: DataSource | None = Field(
        description="The source of the output: human or synthetic.",
        default=None,
    )
    rating: TaskOutputRating | None = Field(
        default=None, description="The rating of the output"
    )

    def validate_output_format(self, task: Task) -> Self:
        # validate output
        if task.output_json_schema is not None:
            try:
                validate_schema(json.loads(self.output), task.output_json_schema)
            except json.JSONDecodeError:
                raise ValueError("Output is not a valid JSON object")
            except jsonschema.exceptions.ValidationError as e:
                raise ValueError(f"Output does not match task output schema: {e}")
        return self

    @model_validator(mode="after")
    def validate_output_source(self, info: ValidationInfo) -> Self:
        # On strict mode and not loaded from file, we validate output_source is not None.
        # We want to be able to load any data, even if it's not perfect. But we want to create perfect data when adding new data.
        if not strict_mode():
            return self
        if self.loaded_from_file(info):
            return self
        if self.source is None:
            raise ValueError("Output source is required when strict mode is enabled")
        return self


class FineTuneStatusType(str, Enum):
    """
    The status type of a fine-tune (running, completed, failed, etc).
    """

    unknown = "unknown"  # server error
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class StructuredOutputMode(str, Enum):
    """
    Enumeration of supported structured output modes.

    - default: let the adapter decide
    - json_schema: request json using API capabilities for json_schema
    - function_calling: request json using API capabilities for function calling
    - json_mode: request json using API's JSON mode, which should return valid JSON, but isn't checking/passing the schema
    - json_instructions: append instructions to the prompt to request json matching the schema. No API capabilities are used. You should have a custom parser on these models as they will be returning strings.
    - json_instruction_and_object: append instructions to the prompt to request json matching the schema. Also request the response as json_mode via API capabilities (returning dictionaries).
    """

    default = "default"
    json_schema = "json_schema"
    function_calling = "function_calling"
    json_mode = "json_mode"
    json_instructions = "json_instructions"
    json_instruction_and_object = "json_instruction_and_object"


class FinetuneDataStrategy(str, Enum):
    final_only = "final_only"
    final_and_intermediate = "final_and_intermediate"


class Finetune(KilnParentedModel):
    """
    The Kiln fine-tune datamodel.

    Initially holds a reference to a training job, with needed identifiers to update the status. When complete, contains the new model ID.
    """

    name: str = NAME_FIELD
    description: str | None = Field(
        default=None,
        description="A description of the fine-tune for you and your team. Not used in training.",
    )
    structured_output_mode: StructuredOutputMode | None = Field(
        default=None,
        description="The mode to use to train the model for structured output, if it was trained with structured output. Will determine how we call the tuned model, so we call with the matching mode.",
    )
    provider: str = Field(
        description="The provider to use for the fine-tune (e.g. 'openai')."
    )
    base_model_id: str = Field(
        description="The id of the base model to use for the fine-tune. This string relates to the provider's IDs for their own models, not Kiln IDs."
    )
    provider_id: str | None = Field(
        default=None,
        description="The ID of the fine-tune job on the provider's side. May not be the same as the fine_tune_model_id.",
    )
    fine_tune_model_id: str | None = Field(
        default=None,
        description="The ID of the fine-tuned model on the provider's side. May not be the same as the provider_id.",
    )
    dataset_split_id: str = Field(
        description="The ID of the dataset split to use for this fine-tune.",
    )
    train_split_name: str = Field(
        default="train",
        description="The name of the training split to use for this fine-tune.",
    )
    validation_split_name: str | None = Field(
        default=None,
        description="The name of the validation split to use for this fine-tune. Optional.",
    )
    parameters: dict[str, str | int | float | bool] = Field(
        default={},
        description="The parameters to use for this fine-tune. These are provider-specific.",
    )
    system_message: str = Field(
        description="The system message to use for this fine-tune.",
    )
    latest_status: FineTuneStatusType = Field(
        default=FineTuneStatusType.unknown,
        description="The latest known status of this fine-tune. Not updated in real time.",
    )
    properties: Dict[str, str | int | float] = Field(
        default={},
        description="Properties of the fine-tune. Different providers may use different properties.",
    )
    data_strategy: FinetuneDataStrategy = Field(
        default=FinetuneDataStrategy.final_only,
        description="The strategy to use for training the model. 'final_only' will only train on the final response. 'final_and_intermediate' will train on the final response and intermediate outputs (chain of thought or reasoning).",
    )

    def parent_task(self) -> Task | None:
        if not isinstance(self.parent, Task):
            return None
        return self.parent


class DataSourceType(str, Enum):
    """
    The source type of a piece of data.

    Human: a human created the data
    Synthetic: a model created the data
    """

    human = "human"
    synthetic = "synthetic"


class DataSourceProperty(BaseModel):
    """
    Defines a property that can be associated with a data source.

    Includes validation rules for when properties are required or not allowed
    based on the data source type.
    """

    name: str
    type: Type[Union[str, int, float]]
    required_for: List[DataSourceType] = []
    not_allowed_for: List[DataSourceType] = []


class DataSource(BaseModel):
    """
    Represents the origin of data, either human or synthetic, with associated properties.

    Properties vary based on the source type - for synthetic sources this includes
    model information, for human sources this includes creator information.
    """

    type: DataSourceType
    properties: Dict[str, str | int | float] = Field(
        default={},
        description="Properties describing the data source. For synthetic things like model. For human, the human's name.",
    )

    _data_source_properties = [
        DataSourceProperty(
            name="created_by",
            type=str,
            required_for=[DataSourceType.human],
            not_allowed_for=[DataSourceType.synthetic],
        ),
        DataSourceProperty(
            name="model_name",
            type=str,
            required_for=[DataSourceType.synthetic],
            not_allowed_for=[DataSourceType.human],
        ),
        DataSourceProperty(
            name="model_provider",
            type=str,
            required_for=[DataSourceType.synthetic],
            not_allowed_for=[DataSourceType.human],
        ),
        DataSourceProperty(
            name="adapter_name",
            type=str,
            required_for=[DataSourceType.synthetic],
            not_allowed_for=[DataSourceType.human],
        ),
        DataSourceProperty(
            name="prompt_builder_name",
            type=str,
            not_allowed_for=[DataSourceType.human],
        ),
        DataSourceProperty(
            # Optional prompt builders with IDs (like static prompts)
            name="prompt_id",
            type=str,
            not_allowed_for=[DataSourceType.human],
        ),
    ]

    @model_validator(mode="after")
    def validate_type(self) -> "DataSource":
        if self.type not in DataSourceType:
            raise ValueError(f"Invalid data source type: {self.type}")
        return self

    @model_validator(mode="after")
    def validate_properties(self) -> "DataSource":
        for prop in self._data_source_properties:
            # Check the property type is correct
            if prop.name in self.properties:
                if not isinstance(self.properties[prop.name], prop.type):
                    raise ValueError(
                        f"'{prop.name}' must be of type {prop.type.__name__} for {self.type} data source"
                    )
            # Check the property is required for the data source type
            if self.type in prop.required_for:
                if prop.name not in self.properties:
                    raise ValueError(
                        f"'{prop.name}' is required for {self.type} data source"
                    )
            # Check the property is not allowed for the data source type
            elif self.type in prop.not_allowed_for and prop.name in self.properties:
                raise ValueError(
                    f"'{prop.name}' is not allowed for {self.type} data source"
                )
        return self

    @model_validator(mode="after")
    def validate_no_empty_properties(self) -> Self:
        for prop, value in self.properties.items():
            if isinstance(value, str) and value == "":
                raise ValueError(
                    f"Property '{prop}' must be a non-empty string for {self.type} data source"
                )
        return self


class TaskRun(KilnParentedModel):
    """
    Represents a single execution of a Task.

    Contains the input used, its source, the output produced, and optional
    repair information if the output needed correction.
    """

    input: str = Field(
        description="The inputs to the task. JSON formatted for structured input, plaintext for unstructured input."
    )
    input_source: DataSource | None = Field(
        default=None, description="The source of the input: human or synthetic."
    )

    output: TaskOutput = Field(description="The output of the task run.")
    repair_instructions: str | None = Field(
        default=None,
        description="Instructions for fixing the output. Should define what is wrong, and how to fix it. Will be used by models for both generating a fixed output, and evaluating future models.",
    )
    repaired_output: TaskOutput | None = Field(
        default=None,
        description="An version of the output with issues fixed. This must be a 'fixed' version of the existing output, and not an entirely new output. If you wish to generate an ideal curatorial output for this task unrelated to this output, generate a new TaskOutput with type 'human' instead of using this field.",
    )
    intermediate_outputs: Dict[str, str] | None = Field(
        default=None,
        description="Intermediate outputs from the task run. Keys are the names of the intermediate output steps (cot=chain of thought, etc), values are the output data.",
    )
    tags: List[str] = Field(
        default=[],
        description="Tags for the task run. Tags are used to categorize task runs for filtering and reporting.",
    )

    def parent_task(self) -> Task | None:
        if not isinstance(self.parent, Task):
            return None
        return self.parent

    @model_validator(mode="after")
    def validate_input_format(self, info: ValidationInfo) -> Self:
        # Don't validate if loading from file (not new). Too slow.
        # We don't allow changing task schema, so this is redundant validation.
        # Note: we still validate if editing a loaded model
        if self.loading_from_file(info):
            # Consider loading an existing model as validated.
            self._last_validated_input = self.input
            return self

        # Don't validate if input has not changed. Too slow to run this every time.
        if (
            hasattr(self, "_last_validated_input")
            and self.input == self._last_validated_input
        ):
            return self

        task = self.parent_task()
        if task is None:
            # don't validate this relationship until we have a path or parent. Give them time to build it (but will catch it before saving)
            return self

        # validate output
        if task.input_json_schema is not None:
            try:
                validate_schema(json.loads(self.input), task.input_json_schema)
            except json.JSONDecodeError:
                raise ValueError("Input is not a valid JSON object")
            except jsonschema.exceptions.ValidationError as e:
                raise ValueError(f"Input does not match task input schema: {e}")
        self._last_validated_input = self.input
        return self

    @model_validator(mode="after")
    def validate_output_format(self, info: ValidationInfo) -> Self:
        # Don't validate if loading from file (not new). Too slow.
        # Note: we still validate if editing a loaded model's output.
        if self.loading_from_file(info):
            # Consider loading an existing model as validated.
            self._last_validated_output = self.output.output if self.output else None
            return self

        # Don't validate unless output has changed since last validation.
        # The validator is slow and costly, don't want it running when setting other fields.
        if (
            hasattr(self, "_last_validated_output")
            and self.output is not None
            and self.output.output == self._last_validated_output
        ):
            return self

        task = self.parent_task()
        if task is None:
            return self

        self.output.validate_output_format(task)
        self._last_validated_output = self.output.output if self.output else None
        return self

    @model_validator(mode="after")
    def validate_repaired_output(self) -> Self:
        if self.repaired_output is not None:
            if self.repaired_output.rating is not None:
                raise ValueError(
                    "Repaired output rating must be None. Repaired outputs are assumed to have a perfect rating, as they have been fixed."
                )
        if self.repair_instructions is None and self.repaired_output is not None:
            raise ValueError(
                "Repair instructions are required if providing a repaired output."
            )
        if self.repair_instructions is not None and self.repaired_output is None:
            raise ValueError(
                "A repaired output is required if providing repair instructions."
            )
        return self

    @model_validator(mode="after")
    def validate_input_source(self, info: ValidationInfo) -> Self:
        # On strict mode and not loaded from file, we validate input_source is not None.
        # We want to be able to load any data, even if it's not perfect. But we want to create perfect data when adding new data.
        if not strict_mode():
            return self
        if self.loaded_from_file(info):
            return self
        if self.input_source is None:
            raise ValueError("input_source is required when strict mode is enabled")
        return self

    @model_validator(mode="after")
    def validate_tags(self) -> Self:
        for tag in self.tags:
            if not tag:
                raise ValueError("Tags cannot be empty strings")
            if " " in tag:
                raise ValueError("Tags cannot contain spaces. Try underscores.")

        return self


# Define the type alias for clarity
DatasetFilter = Callable[[TaskRun], bool]


def AllDatasetFilter(_: TaskRun) -> bool:
    return True


def HighRatingDatasetFilter(task_run: TaskRun) -> bool:
    if task_run.output is None:
        return False
    if task_run.repaired_output is not None:
        # Repairs always considered high quality
        return True
    if task_run.output.rating is None:
        return False
    return task_run.output.rating.is_high_quality()


class DatasetSplitDefinition(BaseModel):
    """
    A definition of a split in a dataset.

    Example: name="train", description="The training set", percentage=0.8 (80% of the dataset)
    """

    name: str = NAME_FIELD
    description: str | None = Field(
        default=None,
        description="A description of the dataset for you and your team. Not used in training.",
    )
    percentage: float = Field(
        ge=0.0,
        le=1.0,
        description="The percentage of the dataset that this split represents (between 0 and 1).",
    )


AllSplitDefinition: list[DatasetSplitDefinition] = [
    DatasetSplitDefinition(name="all", percentage=1.0)
]
Train80Test20SplitDefinition: list[DatasetSplitDefinition] = [
    DatasetSplitDefinition(name="train", percentage=0.8),
    DatasetSplitDefinition(name="test", percentage=0.2),
]
Train60Test20Val20SplitDefinition: list[DatasetSplitDefinition] = [
    DatasetSplitDefinition(name="train", percentage=0.6),
    DatasetSplitDefinition(name="test", percentage=0.2),
    DatasetSplitDefinition(name="val", percentage=0.2),
]
Train80Test10Val10SplitDefinition: list[DatasetSplitDefinition] = [
    DatasetSplitDefinition(name="train", percentage=0.8),
    DatasetSplitDefinition(name="test", percentage=0.1),
    DatasetSplitDefinition(name="val", percentage=0.1),
]


class DatasetSplit(KilnParentedModel):
    """
    A collection of task runs, with optional splits (train, test, validation).

    Used to freeze a dataset into train/test/validation splits for repeatable fine-tuning or other tasks.

    Maintains a list of IDs for each split, to avoid data duplication.
    """

    name: str = NAME_FIELD
    description: str | None = Field(
        default=None,
        description="A description of the dataset for you and your team. Not used in training.",
    )
    splits: list[DatasetSplitDefinition] = Field(
        default_factory=list,
        description="The splits in the dataset.",
    )
    split_contents: dict[str, list[str]] = Field(
        description="The contents of each split in the dataset. The key is the split name, and the value is a list of task run IDs.",
    )

    @model_validator(mode="after")
    def validate_split_percentages(self) -> "DatasetSplit":
        total = sum(split.percentage for split in self.splits)
        if not math.isclose(total, 1.0, rel_tol=1e-9):
            raise ValueError(f"The sum of split percentages must be 1.0 (got {total})")
        return self

    @classmethod
    def from_task(
        cls,
        name: str,
        task: "Task",
        splits: list[DatasetSplitDefinition],
        filter: DatasetFilter = AllDatasetFilter,
        description: str | None = None,
    ):
        """
        Build a dataset split from a task.
        """
        split_contents = cls.build_split_contents(task, splits, filter)
        return cls(
            parent=task,
            name=name,
            description=description,
            splits=splits,
            split_contents=split_contents,
        )

    @classmethod
    def build_split_contents(
        cls,
        task: "Task",
        splits: list[DatasetSplitDefinition],
        filter: DatasetFilter,
    ) -> dict[str, list[str]]:
        valid_ids = []
        for task_run in task.runs():
            if filter(task_run):
                valid_ids.append(task_run.id)

        # Shuffle and split by split percentage
        random.shuffle(valid_ids)
        split_contents = {}
        start_idx = 0
        remaining_items = len(valid_ids)

        # Handle all splits except the last one
        for split in splits[:-1]:
            split_size = round(len(valid_ids) * split.percentage)
            split_contents[split.name] = valid_ids[start_idx : start_idx + split_size]
            start_idx += split_size
            remaining_items -= split_size

        # Last split gets all remaining items (for rounding)
        if splits:
            split_contents[splits[-1].name] = valid_ids[start_idx:]

        return split_contents

    def parent_task(self) -> "Task | None":
        # inline import to avoid circular import
        from kiln_ai.datamodel import Task

        if not isinstance(self.parent, Task):
            return None
        return self.parent

    def missing_count(self) -> int:
        """
        Returns:
            int: the number of task runs that have an ID persisted in this dataset split, but no longer exist in the dataset
        """
        parent = self.parent_task()
        if parent is None:
            raise ValueError("DatasetSplit has no parent task")

        runs = parent.runs(readonly=True)
        all_ids = set(run.id for run in runs)
        all_ids_in_splits = set()
        for ids in self.split_contents.values():
            all_ids_in_splits.update(ids)
        missing = all_ids_in_splits - all_ids
        return len(missing)


class Prompt(KilnParentedModel):
    """
    A prompt for a task.
    """

    name: str = NAME_FIELD
    prompt: str = Field(
        description="The prompt for the task.",
        min_length=1,
    )
    chain_of_thought_instructions: str | None = Field(
        default=None,
        description="Instructions for the model 'thinking' about the requirement prior to answering. Used for chain of thought style prompting. COT will not be used unless this is provided.",
    )


class TaskRequirement(BaseModel):
    """
    Defines a specific requirement that should be met by task outputs.

    Includes an identifier, name, description, instruction for meeting the requirement,
    priority level, and rating type (five_star, pass_fail, pass_fail_critical, custom).
    """

    id: ID_TYPE = ID_FIELD
    name: str = SHORT_NAME_FIELD
    description: str | None = Field(default=None)
    instruction: str = Field(min_length=1)
    priority: Priority = Field(default=Priority.p2)
    type: TaskOutputRatingType = Field(default=TaskOutputRatingType.five_star)


class TaskDeterminism(str, Enum):
    """
    Defines how strictly task outputs should match expected results.

    - deterministic: Requires exact matches
    - semantic_match: Allows different wording with same meaning
    - flexible: Allows variation in both wording and meaning within requirements
    """

    deterministic = "deterministic"  # Expect exact match
    semantic_match = "semantic_match"  # Expect same meaning, but flexible on expression of the meaning
    flexible = "flexible"  # Flexible on semantic output. Eval should be custom based on parsing requirements.


class Task(
    KilnParentedModel,
    KilnParentModel,
    parent_of={
        "runs": TaskRun,
        "dataset_splits": DatasetSplit,
        "finetunes": Finetune,
        "prompts": Prompt,
    },
):
    """
    Represents a specific task to be performed, with associated requirements and validation rules.

    Contains the task definition, requirements, input/output schemas, and maintains
    a collection of task runs.
    """

    name: str = NAME_FIELD
    description: str | None = Field(
        default=None,
        description="A description of the task for you and your team. Will not be used in prompts/training/validation.",
    )
    instruction: str = Field(
        min_length=1,
        description="The instructions for the task. Will be used in prompts/training/validation.",
    )
    requirements: List[TaskRequirement] = Field(default=[])
    output_json_schema: JsonObjectSchema | None = None
    input_json_schema: JsonObjectSchema | None = None
    thinking_instruction: str | None = Field(
        default=None,
        description="Instructions for the model 'thinking' about the requirement prior to answering. Used for chain of thought style prompting.",
    )

    def output_schema(self) -> Dict | None:
        if self.output_json_schema is None:
            return None
        return schema_from_json_str(self.output_json_schema)

    def input_schema(self) -> Dict | None:
        if self.input_json_schema is None:
            return None
        return schema_from_json_str(self.input_json_schema)

    # These wrappers help for typechecking. TODO P2: fix this in KilnParentModel
    def runs(self, readonly: bool = False) -> list[TaskRun]:
        return super().runs(readonly=readonly)  # type: ignore

    def dataset_splits(self, readonly: bool = False) -> list[DatasetSplit]:
        return super().dataset_splits(readonly=readonly)  # type: ignore

    def finetunes(self, readonly: bool = False) -> list[Finetune]:
        return super().finetunes(readonly=readonly)  # type: ignore

    def prompts(self, readonly: bool = False) -> list[Prompt]:
        return super().prompts(readonly=readonly)  # type: ignore


class Project(KilnParentModel, parent_of={"tasks": Task}):
    """
    A collection of related tasks.

    Projects organize tasks into logical groups and provide high-level descriptions
    of the overall goals.
    """

    name: str = NAME_FIELD
    description: str | None = Field(
        default=None,
        description="A description of the project for you and your team. Will not be used in prompts/training/validation.",
    )

    # Needed for typechecking. TODO P2: fix this in KilnParentModel
    def tasks(self) -> list[Task]:
        return super().tasks()  # type: ignore
