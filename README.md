# Basic Transformer implementation for MT

## Clients

Preprocess:
* `learnBPE_cli` : Learns BPE model after applying tokenization ("aggressive", joiner_annotate=True, segment_numbers=True)
* `buildVOC_cli` : Builds vocabulary given tokenization
* `tokenTXT_cli` : Tokenizes raw data
* `buildIDX_cli` : Builds batches from raw data given tokenization and vocabularies

Network:
* `create_cli` : Creates network
* `learn_cli` : Runs learning 
* `translate_cli`: Runs inference

## Usage example:

Given train/valid/test raw (untokenized) datasets:

`$TRAIN`, `$VALID` and `$TEST` indicate the suffix of each dataset while `$SS` and `$TT` indicate source and target sides of parallel data.


### (1) Preprocess

* Build `$BPE` Model:

```
cat $TRAIN.{$SS,$TT} | python3 learnBPE_cli.py $BPE
```
(A single BPE model is built for source and target sides of parallel data)


* Create tokenization config file `$TOK` containing:

```
mode: aggressive
joiner_annotate: True
segment_numbers: True
bpe_model_path: $BPE
```

* Build Vocabularies:

```
cat $TRAIN.$SS | python3 buildVOC_cli.py -tokenizer_config $TOK -max_size 32768 > $VOC.$SS
cat $TRAIN.$TT | python3 buildVOC_cli.py -tokenizer_config $TOK -max_size 32768 > $VOC.$TT
```
(Both source and target vocabularies are built with at most 32K tokens)

### (2) Create network

```
python3 ./create_cli.py -dnet $DNET -src_vocab $VOC.$SS -tgt_vocab $VOC.$TT -src_token $TOK -tgt_token $TOK
```
(Builds network with default parameters; creates $DNET directory and copies files: network, src_voc, tgt_voc, src_tok, tgt_tok, src_bpe, tgt_bpe)

### (3) Learning
```
python3 ./train_cli.py -dnet $DNET -src_train $TRAIN.$SS -tgt_train $TRAIN.$TT -src_valid $VALID.$SS -tgt_valid $VALID.$TT
```

### (4) Inference
```
python3 ./translate_cli.py -dnet $DNET -i $TEST.$SS
```


