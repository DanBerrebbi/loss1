# -*- coding: utf-8 -*-

import sys
import os
import logging
import torch
import math
import numpy as np
import glob
from Vocab import Vocab

def numparameters(model):
  npars = 0 #pars
  nbytes = 0 #bytes
  for name, param in model.named_parameters():
    if param.requires_grad: #learnable parameters only
      npars += param.numel()
      nbytes += param.numel() * param.data.element_size() #returns size of each parameter
      logging.debug("{} => {} = {} x {} bytes".format(name, list(param.data.size()), param.data.numel(), param.data.element_size()))

  name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
  if nbytes == 0:
    i = 0
  else:
    i = int(math.floor(math.log(nbytes, 1024)))
    p = math.pow(1024, i)
    nbytes /= p
  size = "{:.2f}{}".format(nbytes, name[i])

  return npars, size

def save_checkpoint(suffix, model, optimizer, step, keep_last_n):
  checkpoint = { 'step': step, 'model': model.state_dict(), 'optimizer': optimizer.state_dict() }
  torch.save(checkpoint, "{}.checkpoint_{:08d}.pt".format(suffix,step))
  logging.info('Saved {}.checkpoint_{:08d}.pt'.format(suffix,step))
  files = sorted(glob.glob(suffix + '.checkpoint_????????.pt')) 
  while keep_last_n > 0 and len(files) > keep_last_n:
    f = files.pop(0)
    os.remove(f) ### first is the oldest
    logging.debug('Removed checkpoint {}'.format(f))

def load_checkpoint(suffix, model, device):
  step = 0
  files = sorted(glob.glob("{}.checkpoint_????????.pt".format(suffix))) ### I check if there is one model
  if len(files) == 0:
    logging.error('No checkpoint found')
    sys.exit()

  file = files[-1] ### last is the newest
  logging.info('Loading checkpoint file={}'.format(file))
  checkpoint = torch.load(file, map_location=device)
  step = checkpoint['step']
  logging.info('Checkpoint step={}'.format(step))
  ### assert checkpoint['model'] has same options than model
  model.load_state_dict(checkpoint['model'])
  return model

def load_checkpoint_or_initialise(suffix, model, optimizer, device):
  step = 0
  files = sorted(glob.glob("{}.checkpoint_????????.pt".format(suffix))) ### I check if there is one model
  if len(files) == 0:
    for p in model.parameters():
      if p.dim() > 1:
        torch.nn.init.xavier_uniform_(p)
    logging.info('No model found [network initialised]')
    return step, model, optimizer

  file = files[-1] ### last is the newest
  logging.info('Loading checkpoint file={}'.format(file))
  checkpoint = torch.load(file, map_location=device)
  step = checkpoint['step']
  ### assert checkpoint['model'] has same options than model
  model.load_state_dict(checkpoint['model'])
  if optimizer is None:
    logging.info('Loaded model step={} from {}'.format(step,file))
    return step, model, optimizer ### this is for inference

  optimizer.load_state_dict(checkpoint['optimizer'])
  logging.info('Loaded model/optimizer step={} from {}'.format(step,file))
  return step, model, optimizer ### this is for learning

##############################################################################################################
### Endcoder_Decoder #########################################################################################
##############################################################################################################
class Encoder_Decoder(torch.nn.Module):
  def __init__(self, n_layers, ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout, src_voc_size, tgt_voc_size, idx_pad): 
    super(Encoder_Decoder, self).__init__()
    self.idx_pad = idx_pad
    self.src_emb = torch.nn.Embedding(src_voc_size, emb_dim, padding_idx=idx_pad)
    self.tgt_emb = torch.nn.Embedding(tgt_voc_size, emb_dim, padding_idx=idx_pad)
    self.add_pos_enc = AddPositionalEncoding(emb_dim, dropout, max_len=5000)
    self.stacked_encoder = Stacked_Encoder(n_layers, ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout)
    self.stacked_decoder = Stacked_Decoder(n_layers, ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout)
    self.generator = Generator(emb_dim, tgt_voc_size)
    logging.debug('n_layers={}'.format(n_layers))
    logging.debug('ff_dim={}'.format(ff_dim))
    logging.debug('n_heads={}'.format(n_heads))
    logging.debug('emb_dim={}'.format(emb_dim))
    logging.debug('qk_dim={}'.format(qk_dim))
    logging.debug('v_dim={}'.format(v_dim))
    logging.debug('Vs={}'.format(src_voc_size))
    logging.debug('Vt={}'.format(tgt_voc_size))

  def forward(self, src, tgt, msk_src, msk_tgt):
    #src is [bs,ls]
    #tgt is [bs,lt]
    #msk_src is [bs,1,ls] (False where <pad> True otherwise)
    #mst_tgt is [bs,lt,lt]
    src = self.add_pos_enc(self.src_emb(src)) #[bs,ls,ed]
    tgt = self.add_pos_enc(self.tgt_emb(tgt)) #[bs,lt,ed]
    assert src.shape[0] == tgt.shape[0] ### src/tgt batch_sizes must be equal
    z_src = self.stacked_encoder(src, msk_src) #[bs,ls,ed]
    z_tgt = self.stacked_decoder(z_src, tgt, msk_src, msk_tgt) #[bs,lt,ed]
    y = self.generator(z_tgt) #[bs, lt, Vt]
    return y

  def encode(self, src, msk_src):
    src = self.add_pos_enc(self.src_emb(src)) #[bs,ls,ed]
    z_src = self.stacked_encoder(src, msk_src) #[bs,ls,ed]
    return z_src

  def decode(self, z_src, tgt, msk_src, msk_tgt=None): 
    assert z_src.shape[0] == tgt.shape[0] ### src/tgt batch_sizes must be equal
    #this is used on inference
    #z_src are the embeddings of the source words (encoder) [bs, sl, ed]
    #tgt is the history (words already generated) for current step [bs, lt]
    #initially tgt contains only <eos> 
    #logging.info('tgt = {}'.format(tgt.shape))
    tgt = self.add_pos_enc(self.tgt_emb(tgt)) #[bs,lt,ed]
    #logging.info('tgt = {}'.format(tgt.shape))
    z_tgt = self.stacked_decoder(z_src, tgt, msk_src, msk_tgt) #[bs,lt,ed]
    #logging.info('z_tgt = {}'.format(z_tgt.shape))
    y = self.generator(z_tgt) #[bs, lt, Vt]
    #logging.info('y = {}'.format(y.shape))
    return y

##############################################################################################################
### Stacked_Encoder ##########################################################################################
##############################################################################################################
class Stacked_Encoder(torch.nn.Module):
  def __init__(self, n_layers, ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout): 
    super(Stacked_Encoder, self).__init__()
    self.encoderlayers = torch.nn.ModuleList([Encoder(ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout) for _ in range(n_layers)])
    self.norm = torch.nn.LayerNorm(emb_dim, eps=1e-6) 

  def forward(self, src, msk):
    for i,encoderlayer in enumerate(self.encoderlayers):
      src = encoderlayer(src, msk) #[bs, ls, ed]
    return self.norm(src)

##############################################################################################################
### Stacked_Decoder ##########################################################################################
##############################################################################################################
class Stacked_Decoder(torch.nn.Module):
  def __init__(self, n_layers, ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout): 
    super(Stacked_Decoder, self).__init__()
    self.decoderlayers = torch.nn.ModuleList([Decoder(ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout) for _ in range(n_layers)])
    self.norm = torch.nn.LayerNorm(emb_dim, eps=1e-6) 

  def forward(self, z_src, tgt, msk_src, msk_tgt):
    for i,decoderlayer in enumerate(self.decoderlayers):
      tgt = decoderlayer(z_src, tgt, msk_src, msk_tgt)
    return self.norm(tgt)

##############################################################################################################
### Encoder ##################################################################################################
##############################################################################################################
class Encoder(torch.nn.Module):
  def __init__(self, ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout):
    super(Encoder, self).__init__()
    self.feedforward = FeedForward(emb_dim, ff_dim, dropout)
    self.multihead_attn = MultiHead_Attn(n_heads, emb_dim, qk_dim, v_dim, dropout)
    self.norm_att = torch.nn.LayerNorm(emb_dim, eps=1e-6) 
    self.norm_ff = torch.nn.LayerNorm(emb_dim, eps=1e-6) 

  def forward(self, src, msk):
    src_norm = self.norm_att(src)
    tmp = self.multihead_attn(q=src_norm, k=src_norm, v=src_norm, msk=msk) + src #[bs, ls, ed] contains dropout
    tmp_norm = self.norm_ff(tmp)
    z = self.feedforward(tmp_norm) + tmp #[bs, ls, ed] contains dropout
    return z

##############################################################################################################
### Decoder ##################################################################################################
##############################################################################################################
class Decoder(torch.nn.Module):
  def __init__(self, ff_dim, n_heads, emb_dim, qk_dim, v_dim, dropout):
    super(Decoder, self).__init__()
    self.feedforward = FeedForward(emb_dim, ff_dim, dropout)
    self.multihead_attn = MultiHead_Attn(n_heads, emb_dim, qk_dim, v_dim, dropout)
    self.norm_att1 = torch.nn.LayerNorm(emb_dim, eps=1e-6) 
    self.norm_att2 = torch.nn.LayerNorm(emb_dim, eps=1e-6) 
    self.norm_ff = torch.nn.LayerNorm(emb_dim, eps=1e-6) 

  def forward(self, z_src, tgt, msk_src, msk_tgt):
    tgt_norm = self.norm_att1(tgt)
    #attention over tgt (previous) words : q, k, v are tgt words
    tmp = self.multihead_attn(q=tgt_norm, k=tgt_norm, v=tgt_norm, msk=msk_tgt) + tgt #[bs, lt, ed] contains dropout
    tmp_norm = self.norm_att2(tmp)
    #attention over src words : q are tgt words, k, v are src words
    tmp = self.multihead_attn(q=tmp_norm, k=z_src,    v=z_src,    msk=msk_src) + tmp #[bs, lt, ed] contains dropout
    tmp_norm = self.norm_ff(tmp)
    z = self.feedforward(tmp_norm) + tmp #[bs, lt, ed] contains dropout
    return z


##############################################################################################################
### MultiHead_Attn ###########################################################################################
##############################################################################################################
class MultiHead_Attn(torch.nn.Module):
  def __init__(self, n_heads, emb_dim, qk_dim, v_dim, dropout):
    super(MultiHead_Attn, self).__init__()
    self.nh = n_heads
    self.ed = emb_dim
    self.qd = qk_dim
    self.kd = qk_dim
    self.vd = v_dim
    self.WQ = torch.nn.Linear(emb_dim, qk_dim*n_heads)
    self.WK = torch.nn.Linear(emb_dim, qk_dim*n_heads)
    self.WV = torch.nn.Linear(emb_dim,  v_dim*n_heads)
    self.WO = torch.nn.Linear(v_dim*n_heads, emb_dim)
    self.dropout = torch.nn.Dropout(dropout)

  def forward(self, q, k, v, msk=None):
    #q is [bs, slq, ed]
    #k is [bs, slk, ed]
    #v is [bs, slv, ed]
    #msk is [bs, 1, ls] or [bs, lt, lt]
    if msk is not None:
      msk = msk.unsqueeze(1) #[bs, 1, 1, ls] or [bs, 1, lt, lt]
    bs = q.shape[0]
    slq = q.shape[1] 
    slk = k.shape[1]
    slv = v.shape[1]
    ed = q.shape[2]
    assert self.ed == q.shape[2] == k.shape[2] == v.shape[2]
    assert slk == slv #when applied in decoder both refer the source-side (slq refers the target-side)

    Q = self.WQ(q).contiguous().view([bs,slq,self.nh,self.qd]).permute(0,2,1,3) #=> [bs,slq,nh*qd] => [bs,slq,nh,qd] => [bs,nh,slq,qd]
    K = self.WK(k).contiguous().view([bs,slk,self.nh,self.kd]).permute(0,2,1,3) #=> [bs,slk,nh*kd] => [bs,slk,nh,kd] => [bs,nh,slk,kd]
    V = self.WV(v).contiguous().view([bs,slv,self.nh,self.vd]).permute(0,2,1,3) #=> [bs,slv,nh*vd] => [bs,slv,nh,vd] => [bs,nh,slv,vd]
    #Scaled dot-product Attn from multiple Q, K, V vectors (bs*nh*sl vectors)
    s = torch.matmul(Q, K.transpose(2, 3)) / self.kd**0.5 #[bs,nh,slq,qd] x [bs,nh,kd,slk] = [bs,nh,slq,slk] # thanks to qd==kd #in decoder slq are target words and slk are source words
    if msk is not None:
      s = s.masked_fill(msk == 0, -1e9) #score=-1e9 to masked tokens
    w = torch.nn.functional.softmax(s, dim=-1) #[bs,nh,slq,slk] (these are the attention weights)
    z = torch.matmul(w,V) #[bs,nh,slq,slk] x [bs,nh,slv,vd] = [bs,nh,slq,vd] #thanks to slk==slv
    z = z.transpose(1, 2).contiguous().view([bs,slq,-1]) #=> [bs,slq,nh,vd] => [bs,slq,nh*vd]
    z = self.WO(z) #[bs,slq,ed]
    return self.dropout(z)

##############################################################################################################
### FeedForward ##############################################################################################
##############################################################################################################
class FeedForward(torch.nn.Module):
  def __init__(self, emb_dim, ff_dim, dropout):
    super(FeedForward, self).__init__()
    self.FF_in = torch.nn.Linear(emb_dim, ff_dim)
    self.FF_out = torch.nn.Linear(ff_dim, emb_dim)
    self.dropout = torch.nn.Dropout(dropout) #this regularization is not used in the original model

  def forward(self, x):
    tmp = self.FF_in(x)
    tmp = torch.nn.functional.relu(tmp)
    tmp = self.dropout(tmp)
    tmp = self.FF_out(tmp)
    tmp = self.dropout(tmp)
    return tmp

##############################################################################################################
### PositionalEncoding #######################################################################################
##############################################################################################################
class AddPositionalEncoding(torch.nn.Module):
  def __init__(self, emb_dim, dropout, max_len=5000):
    super(AddPositionalEncoding, self).__init__()
    self.dropout = torch.nn.Dropout(dropout)
    pe = torch.zeros(max_len, emb_dim)
    position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
    div_term = torch.exp(torch.arange(0, emb_dim, 2).float() * (-math.log(10000.0) / emb_dim))
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    pe = pe.unsqueeze(0).transpose(0, 1)
    self.register_buffer('pe', pe) #register_buffer are for params saved&restored in state_dict not trained 

  def forward(self, x):
    x = x + self.pe[:x.size(0), :]
    return self.dropout(x)

##############################################################################################################
### Generator ################################################################################################
##############################################################################################################
class Generator(torch.nn.Module):
  def __init__(self, emb_dim, voc_size):
    super(Generator, self).__init__()
    self.proj = torch.nn.Linear(emb_dim, voc_size) #[bs, Vt]

  def forward(self, x):
    y = torch.nn.functional.log_softmax(self.proj(x), dim=-1)
    return y


