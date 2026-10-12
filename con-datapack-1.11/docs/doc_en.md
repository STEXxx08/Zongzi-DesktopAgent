# con-datapack (condata) Documentation

Generated from the project `condata` by **AkagawaTsurunaki and STEXxx** at `2026-04-11T10:34:42.457314`.

> 注：本文档由 `docs/scripts/doc_gen.py` 生成。该脚本当前在 `condata.pipeline.defense`
> 上会 import 失败（该模块引用了已不存在的 `data.pipeline.abs_data`），因此本文档需手工维护
> 或在修复该模块后重新生成。

## condata.data

### Danmaku

*In module `condata.data.danmaku`*.


    Represents a danmaku entity from live-streaming.
    

| Field Name | Type | Description|
|--|--|--|
|`uid`|`<class 'str'>`|The unique identifier of the user who sent this danmaku (depending on the platform).|
|`username`|`<class 'str'>`|The name or handle of the user who sent this danmaku.|
|`content`|`<class 'str'>`|The content of the danmaku.|
|`ts`|`<class 'int'>`|The timestamp of when the danmaku was sent.|


### Gift

*In module `condata.data.danmaku`*.

| Field Name | Type | Description|
|--|--|--|
|`uid`|`<class 'str'>`|Sender ID.|
|`username`|`<class 'str'>`|Sender username.|
|`gift_name`|`<class 'str'>`|Gift name.|
|`num`|`<class 'int'>`|Number of gifts.|


### SuperChat

*In module `condata.data.danmaku`*.


    Represents a Super Chat message from live-streaming.
    

| Field Name | Type | Description|
|--|--|--|
|`uid`|`<class 'str'>`|The unique identifier of the user who sent this danmaku (depending on the platform).|
|`username`|`<class 'str'>`|The name or handle of the user who sent this danmaku.|
|`content`|`<class 'str'>`|The content of the danmaku.|
|`ts`|`<class 'int'>`|The timestamp of when the danmaku was sent.|
|`money`|`<class 'str'>`|The money sent by the user (depending on the platform).|


### TTSPrompt

*In module `condata.data.prompt`*.


    Represents a Text-to-Speech (TTS) prompt.
    

| Field Name | Type | Description|
|--|--|--|
|`audio_path`|`<class 'str'>`|Path to the audio file.|
|`lang`|`<class 'str'>`|Language enum value for the TTS output. Use enumerator `Language`.|
|`sentiment`|`<class 'str'>`|Sentiment tag of the input text.|
|`prompt_text`|`<class 'str'>`|Text to be converted to speech.|


### AppStatus

*In module `condata.data.state`*.


    Represents the status of an application.
    

| Field Name | Type | Description|
|--|--|--|
|`status`|`<enum 'AppStatusEnum'>`|The current status of the application.|


### AppStatusEnum

*In module `condata.data.state`*.


    Enum representing the possible statuses of an application.
    

### ServiceState

*In module `condata.data.state`*.


    Represents the state of a service.
    

| Field Name | Type | Description|
|--|--|--|
|`state`|`<class 'str'>`|The current state of the service.|
|`msg`|`<class 'str'>`|A message describing the state of the service.|


## condata.pipeline

### AbsractImageModelQuery

*In module `condata.pipeline.abs_data`*.


    Abstract class representing an image model query request.

    This class extends the `AbstractModelQuery` class and adds an `img_path` attribute to specify the image path. The image path can be a local file path or a remote file path.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`img_path`|`str \| None`|Image path. <br>Note:<br>1. If `image_path` exists in your local machine, will automatically read as binary stream.<br>2. If `image_path` dose not exist in your local machine, then we assume that it must exists in your remote machine (server, etc.)<br>|


### AbstractModelPrediction

*In module `condata.pipeline.abs_data`*.


    Abstract base class representing a model prediction response.

    This class provides a basic structure for creating model prediction responses. It includes a unique identifier for the prediction, which is automatically generated using UUID.

    [!Note]
        The `id` field is automatically generated if not explicitly provided. This ensures that each query has a unique identifier.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|


### AbstractModelQuery

*In module `condata.pipeline.abs_data`*.


    Abstract base class representing a model query request.

    This class provides a basic structure for creating model query requests. It includes a unique identifier for the query, which is automatically generated using UUID.

    [!Note]
        The `id` field is automatically generated if not explicitly provided. This ensures that each query has a unique identifier.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|


### ASRPrediction

*In module `condata.pipeline.asr`*.


    Represents an Auto-Speech-Recognition (ASR) result.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|
|`transcript`|`<class 'str'>`|Transcribed text from the speech.|


### ASRQuery

*In module `condata.pipeline.asr`*.


    Represents an Auto-Speech-Recognition (ASR) query.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`audio_path`|`<class 'str'>`|Path to the audio file.|
|`media_type`|`<class 'str'>`|Format of audio data.|
|`sample_rate`|`<class 'int'>`|Sample rate of the audio.|
|`channels`|`<class 'int'>`|Number of audio channels.|


### ASRStreamQuery

*In module `condata.pipeline.asr`*.


    Represents an Auto-Speech-Recognition (ASR) stream query.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`is_final`|`<class 'bool'>`|Flag indicating if this is the final chunk of audio.|
|`audio_data`|`<class 'bytes'>`|Raw audio data bytes.|
|`media_type`|`<class 'str'>`|Format of audio data.|
|`sample_rate`|`<class 'int'>`|Sample rate of the audio.|
|`channels`|`<class 'int'>`|Number of audio channels.|


### ImgCapPrediction

*In module `condata.pipeline.img_cap`*.


    Prediction for image captioning model.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|
|`caption`|`<class 'str'>`|The image caption result.|
|`lang`|`<class 'str'>`|The language of the image caption (depending on your model).|


### ImgCapQuery

*In module `condata.pipeline.img_cap`*.


    Query for image captioning model.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`img_path`|`str \| None`|Image path. <br>Note:<br>1. If `image_path` exists in your local machine, will automatically read as binary stream.<br>2. If `image_path` dose not exist in your local machine, then we assume that it must exists in your remote machine (server, etc.)<br>|
|`prompt`|`<class 'str'>`|The prompt for generating the image caption.|


### Conversation

*In module `condata.pipeline.llm`*.


    Message containing information about a conversation.
    Like Langchain Message.
    

| Field Name | Type | Description|
|--|--|--|
|`role`|`<class 'str'>`|The role of this conversation. See `RoleEnum`.|
|`content`|`<class 'str'>`|The content of this conversation.|
|`metadata`|`str \| None`|The metadata of this conversation.|


### DefenseConversation

*In module `condata.pipeline.llm`*.


    Defense models are categorized into 3 types based on their input-output orientation:
    **Input-Level Defense**, **Output-Level Defense**, and **Unified Input-Output Safeguards**.

    Consequently, the detection results should be oriented toward each **Conversation**—specifically,
    determining whether the conversation contains injection content and providing a confidence score for this judgment.

    When utilizing any of these defense models, please encapsulate the processing using **DefenseConversation**.
    The handling logic should branch based on whether the defense model is enabled or not.
    

| Field Name | Type | Description|
|--|--|--|
|`role`|`<class 'str'>`|The role of this conversation. See `RoleEnum`.|
|`content`|`<class 'str'>`|The content of this conversation.|
|`metadata`|`str \| None`|The metadata of this conversation.|
|`defense`|`<class 'condata.pipeline.llm.DefenseInfo'>`|The detection results from the defense model.|


### DefenseInfo

*In module `condata.pipeline.llm`*.


    The detection results from the defense model.
    

| Field Name | Type | Description|
|--|--|--|
|`label`|`typing.Literal['safe', 'injection']`|`safe` indicates the text is free from injection risks, while `injection` indicates the text contains injection risks.|
|`score`|`<class 'float'>`|The score (0.0~1.0) represents the confidence level of this determination.|


### LLMPrediction

*In module `condata.pipeline.llm`*.


    Prediction for Large Language Models.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|
|`response`|`<class 'str'>`|The content of the result.|
|`history`|`list[condata.pipeline.llm.Conversation]`|The history of previous conversations. The current response included.|


### LLMQuery

*In module `condata.pipeline.llm`*.


    Query for Large Language Models.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`text`|`<class 'str'>`|The content of the query.|
|`history`|`list[condata.pipeline.llm.Conversation]`|The history of previous conversations.|


### RoleEnum

*In module `condata.pipeline.llm`*.


    The role that made this conversation.
    

### InsertRow

*In module `condata.pipeline.milvus`*.


    Represents a row to be inserted into a Milvus database table.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'int'>`|Unique identifier for the row.|
|`text`|`<class 'str'>`|Text content of the row.|
|`subject`|`<class 'str'>`|Subject or category of the row.|


### MilvusInsert

*In module `condata.pipeline.milvus`*.


    Represents an insert operation for a Milvus collection.
    

| Field Name | Type | Description|
|--|--|--|
|`collection_name`|`<class 'str'>`|Name of the Milvus collection to insert into.|
|`drop_if_exists`|`<class 'bool'>`|Whether to drop the collection if it exists.|
|`texts`|`typing.List[condata.pipeline.milvus.InsertRow]`|List of rows to be inserted.|


### MilvusInsertResult

*In module `condata.pipeline.milvus`*.


    Represents the result of an insert operation on a Milvus collection.
    

| Field Name | Type | Description|
|--|--|--|
|`insert_count`|`<class 'int'>`|Number of rows successfully inserted.|
|`ids`|`typing.List[int]`|IDs of the inserted rows.|


### MilvusQuery

*In module `condata.pipeline.milvus`*.


    Represents a query operation on a Milvus collection.
    

| Field Name | Type | Description|
|--|--|--|
|`collection_name`|`<class 'str'>`|Name of the Milvus collection to query.|
|`limit`|`<class 'int'>`|Maximum number of results to return (top-k).|
|`output_fields`|`typing.List[str]`|Fields to include in the query results.|
|`query`|`<class 'str'>`|The actual query string.|


### MilvusQueryResult

*In module `condata.pipeline.milvus`*.


    Represents the result of a Milvus query operation.
    

| Field Name | Type | Description|
|--|--|--|
|`result`|`typing.List[typing.List[condata.pipeline.milvus.QueryRow]]`|Nested list containing all query results.|


### QueryRow

*In module `condata.pipeline.milvus`*.


    Represents a row returned by a Milvus query.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'int'>`|Unique identifier for the row.|
|`entity`|`<class 'dict'>`|The actual data from the queried row.|
|`distance`|`<class 'float'>`|Distance metric used in the query.|


### OCRPrediction

*In module `condata.pipeline.ocr`*.


    Prediction result for Optical Character Recognition model.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|
|`region_results`|`typing.List[condata.pipeline.ocr.RegionResult]`|List of results for different regions.|


### OCRQuery

*In module `condata.pipeline.ocr`*.


    Query for Optical Character Recognition (OCR) model.

    This class inherits from AbsractImageModelQuery and doesn't have any specific attributes defined.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`img_path`|`str \| None`|Image path. <br>Note:<br>1. If `image_path` exists in your local machine, will automatically read as binary stream.<br>2. If `image_path` dose not exist in your local machine, then we assume that it must exists in your remote machine (server, etc.)<br>|


### Position

*In module `condata.pipeline.ocr`*.


    Represents the position of a region in an image.
    

| Field Name | Type | Description|
|--|--|--|
|`lu`|`<class 'condata.pipeline.ocr.Vector2D'>`|Left-up corner coordinates.|
|`ru`|`<class 'condata.pipeline.ocr.Vector2D'>`|Right-up corner coordinates.|
|`rd`|`<class 'condata.pipeline.ocr.Vector2D'>`|Right-down corner coordinates.|
|`ld`|`<class 'condata.pipeline.ocr.Vector2D'>`|Left-down corner coordinates.|


### RegionResult

*In module `condata.pipeline.ocr`*.


    Represents the result for a specific region in OCR.
    

| Field Name | Type | Description|
|--|--|--|
|`position`|`<class 'condata.pipeline.ocr.Position'>`|The position of the detected region.|
|`content`|`<class 'str'>`|The transcribed text from the detected region.|
|`confidence`|`<class 'float'>`|The confidence level of the transcription.|


### Vector2D

*In module `condata.pipeline.ocr`*.


    Represents a two-dimensional vector.
    

| Field Name | Type | Description|
|--|--|--|
|`x`|`<class 'float'>`|The x-coordinate of the vector.|
|`y`|`<class 'float'>`|The y-coordinate of the vector.|


### TTSPrediction

*In module `condata.pipeline.tts`*.


    Represents a Text-to-Speech (TTS) result.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|
|`wave_data`|`<class 'bytes'>`|The raw audio data produced by the TTS model.|
|`audio_type`|`<class 'str'>`|The format or type of the audio data.|


### TTSQuery

*In module `condata.pipeline.tts`*.


    Represents a Text-to-Speech (TTS) query.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`text`|`<class 'str'>`|The text to be converted to speech.|
|`text_language`|`<class 'str'>`|The language of the input text.|
|`refer_wav_path`|`<class 'str'>`|Path to the reference WAV file.|
|`prompt_text`|`<class 'str'>`|Text for the reference WAV file.|
|`prompt_language`|`<class 'str'>`|The language of the prompt text.|
|`cut_punc`|`typing.Optional[str]`|Use to split the sentences.|
|`audio_type`|`typing.Literal['raw', 'ogg', 'wav']`|The type of audio to be generated.|


### TTSStreamPrediction

*In module `condata.pipeline.tts`*.


    Represents a stream Text-to-Speech (TTS) result.

    This class encapsulates the output of a TTS model in a streaming format,
    allowing for real-time processing and playback of synthesized speech.
    Each instance of this class corresponds to a single segment of audio data
    generated by the TTS model, along with metadata indicating the sequence
    number and whether the segment is the final one in the stream.

    [!Note]:
        The wave_data attribute should be handled carefully, as it contains binary data.
        Ensure proper decoding and playback mechanisms are in place when working with this attribute.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|
|`seq`|`<class 'int'>`|The sequence number of this prediction object, indicating its position within the overall TTS stream. <br>This allows for proper ordering and synchronization of audio segments.|
|`is_final`|`<class 'bool'>`|A flag indicating whether this prediction object represents the final segment of the TTS stream. <br>If True, it signifies that no more audio data will follow.|
|`wave_data`|`<class 'bytes'>`|The raw audio data generated by the TTS model for this segment. <br>This is typically in the form of a byte stream representing the waveform of the synthesized speech.|
|`audio_type`|`<class 'str'>`|The format or type of the audio data contained in wave_data. <br>This could be a specific codec or container format, such as `wav`, `ogg`, or `raw`, which determines how the audio should be processed and played back.|


### VidCapPrediction

*In module `condata.pipeline.vid_cap`*.


    Prediction result for video captioning model.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|
|`caption`|`<class 'str'>`|The generated caption for the video.|
|`lang`|`<class 'str'>`|The language of the generated caption (depending on your model).|


### VidCapQuery

*In module `condata.pipeline.vid_cap`*.


    Query for video captioning model.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`vid_path`|`<class 'str'>`|Path to the video file.|


### PhoneAction

*In module `condata.pipeline.vla`*.


    Represents an action to be performed on a phone environment.
    

| Field Name | Type | Description|
|--|--|--|
|`env`|`<class 'str'>`|The environment where the action is performed.|
|`action`|`typing.Literal['INPUT', 'SWIPE', 'TAP', 'ANSWER', 'ENTER']`|The type of action to be performed.|
|`value`|`str \| None`|The value associated with the action (optional).|
|`position`|`typing.Optional[typing.List[float]]`|The position coordinates for the action (optional).|


### ShowUiPrediction

*In module `condata.pipeline.vla`*.


    Represents a prediction result from the Show UI model.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the prediction for a model response.|
|`actions`|`typing.List[typing.Union[condata.pipeline.vla.WebAction, condata.pipeline.vla.PhoneAction]]`|The list of actions predicted by the model.|


### ShowUiQuery

*In module `condata.pipeline.vla`*.


    Represents a query for the Show UI model.
    

| Field Name | Type | Description|
|--|--|--|
|`id`|`<class 'str'>`|Unique ID of the query for a model request.|
|`img_path`|`str \| None`|Image path. <br>Note:<br>1. If `image_path` exists in your local machine, will automatically read as binary stream.<br>2. If `image_path` dose not exist in your local machine, then we assume that it must exists in your remote machine (server, etc.)<br>|
|`query`|`<class 'str'>`|The main query string.|
|`env`|`typing.Optional[typing.Literal['web', 'phone']]`|The environment for the query (optional).|
|`system_prompt`|`str \| None`|The system prompt for the query (optional; None for default system prompt).|
|`history`|`typing.List[typing.Union[condata.pipeline.vla.WebAction, condata.pipeline.vla.PhoneAction]]`|The history of actions performed.|


### WebAction

*In module `condata.pipeline.vla`*.


    Represents an action to be performed on a web environment.
    

| Field Name | Type | Description|
|--|--|--|
|`env`|`<class 'str'>`|The environment where the action is performed.|
|`action`|`typing.Literal['CLICK', 'INPUT', 'SELECT', 'HOVER', 'ANSWER', 'ENTER', 'SCROLL', 'SELECT_TEXT', 'COPY']`|The type of action to be performed.|
|`value`|`str \| None`|The value associated with the action (optional).|
|`position`|`typing.Optional[typing.List[float]]`|The position coordinates for the action (optional).|


## condata.protocol

### ZongziProtocol

*In module `condata.protocol.protocol`*.


    Represents a message following the Zongzi protocol.
    

| Field Name | Type | Description|
|--|--|--|
|`protocol`|`<class 'str'>`|The name of the protocol.|
|`version`|`<class 'str'>`|The version of the protocol.|
|`message`|`<class 'str'>`|A descriptive message.|
|`action`|`<class 'str'>`|The action associated with the message.|
|`code`|`<class 'int'>`|A status code.|
|`data`|`~T`|The data payload, which can be of any type.|


