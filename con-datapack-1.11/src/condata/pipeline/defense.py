from enum import Enum
from typing import List

from pydantic import BaseModel, Field
from data.pipeline.abs_data import AbstractModelQuery, AbstractModelPrediction
from data.pipeline.llm import RoleEnum

"""
Defense models are categorized into three types based on their input-output orientation:
**Input-Level Defense**, **Output-Level Defense**, and **Unified Input-Output Safeguards**.

Consequently, the detection results should be oriented toward each **Conversation**—specifically,
determining whether the conversation contains injection content and providing a confidence score for this judgment.

When utilizing any of these defense models, please encapsulate the processing using **DefenseConversation**.
The handling logic should branch based on whether the defense model is enabled or not.
"""


class DefenseResult(str, Enum):
    """
    Classification labels for conversation safety detection.
    """
    injection = 'injection'
    safe = 'safe'


class DefenseConversation(BaseModel):
    """
    Defense message containing information about a conversation.
    """
    role: RoleEnum = Field(default=RoleEnum.user, description="The role of this conversation. See `RoleEnum`.")
    content: str = Field(default=None, description="The content of the query.")
    defense_result: DefenseResult = Field(default=DefenseResult.safe,
                                          description="The defense result of the conversation. See `DefenseResult`.")
    confidence: float = Field(default=None, description="The confidence of the detection.")


class DefenseQuery(AbstractModelQuery):
    """
    Query for Defense Model.
    """
    text: str = Field(default=None, description="The content of the query.")
    role: str = Field(default=RoleEnum.user, description="The role of this query. See `RoleEnum`.")
    history: List[DefenseConversation] = Field(default_factory=list,
                                               description="The history of previous conversations.")


class DefensePrediction(AbstractModelPrediction):
    """
    Prediction from defense model.
    """
    defense_result: DefenseResult = Field(default=DefenseResult.safe,
                                          description="The defense result of the conversation. See `DefenseResult`.")
    confidence: float = Field(default=None, description="The confidence of the detection.")
    history: list[DefenseConversation] = Field(default_factory=list,
                                               description="The history of previous conversations. The current response included.")
