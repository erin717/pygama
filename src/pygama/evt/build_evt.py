"""
This module implements routines to build the evt tier.

TODO:
- make me faster! Currently 37.70 ms/evt
- write tests
- get feedback
- write everything smart
"""
from __future__ import annotations
from importlib import import_module
import itertools
import json
from legendmeta import LegendMetadata
import logging
import numpy as np
import pygama.lgdo.lh5_store as store
from pygama.lgdo import Array
import re
import os

log = logging.getLogger(__name__)

def num_and_pars(value: str,par_dic: dict):
    # function tries to convert a string to a int, float, bool
    # or returns the value if value is a key in par_dic
    if value in par_dic.keys(): return par_dic[value]
    try:
        value = int(value)
    except ValueError:
        try:
            value = float(value)
        except ValueError:
            try:
                value = bool(value)
            except ValueError:
                pass
    return value

def evaluate_expression(f_evt:str,f_hit:str, f_dsp: str, chns: list, mode: str, expr: str, para: dict = None, defv = np.nan, getch: bool = False) -> np.ndarray:
     """
     Evaluates the expression defined by the user across all channels according to the mode
     Parameters
     ----------
     f_evt
        Path to event tier file
     f_hit
        Path to hit tier file
     f_dsp
        Path to dsp tier file
     chns
        List of channel names across which expression gets evaluated (form: "ch<rawid>")
     mode
        The mode determines how the event entry is calculated across channels. Options are:
        - "first": The value of the channel in an event triggering first in time (according to tp_0_est) is returned. It  is possible to add a condition (e.g. "first>10"). Only channels fullfilling this condition are considered in the time evaluation. If no channel fullfilles the condition, nan is returned for this event.
        - "last": The value of the channel in an event triggering last in time (according to tp_0_est) is returned. It  is possible to add a condition (e.g. "last>10"). Only channels fullfilling this condition are considered in the time evaluation. If no channel fullfilles the condition, nan is returned for this event.
        - "tot": The sum of all channels across an event. It  is possible to add a condition (e.g. "tot>10"). Only channels fullfilling this condition are considered in the time evaluation. If no channel fullfilles the condition, zero is returned for this event. Booleans are treated as integers 0/1.
        - "any": Logical or between all channels. Non boolean values are True for values != 0 and False for values == 0.
        - "all": Logical and between all channels. Non boolean values are True for values != 0 and False for values == 0.
        - ch_field: A previously generated channel_id field (i.e. from the get_ch flage) can be given here, and the value of this specific channels is used.
        - "single": !!!NOT IMPLEMENTED!!!. Channels are not combined, but result saved for each channel. field name gets channel id as suffix.
     expr
        The expression. That can be any mathematical equation/comparison. If mode == func, the expression needs to be a special processing function defined in modules (e.g. "modules.spm.get_energy). In the expression parameters from either hit, dsp, evt tier (from operations performed before this one! --> JSON operaions order matters), or from the "parameters" field can be used.
     para
        Dictionary of parameters defined in the "parameters" field in the configuration JSON file. 
     getch
        Only affects "first", "last" modes. In that cases the rawid of the resulting values channel is returned as well.
     """
     #define dimension of output array
     out = np.zeros(store.LH5Store().read_n_rows(chns[0]+"/dsp/",f_dsp))
     out[:] = defv
     out_chs =  np.zeros(len(out),dtype=int)
     
     if mode == "func":
        exprl = re.findall(r"[a-zA-Z_$][\w$]*",expr)
        var = {}
        if os.path.exists(f_evt):var = store.load_nda(f_evt,[e.split('/')[-1] for e in store.ls(f_evt) if e.split('/')[-1] in exprl])
        if para: var = var | para
        
        # evaluate expression
        func, params = expr.split('(')
        params = params[:-1].split(',')
        params = [f_hit,f_dsp,chns]+[num_and_pars(e,var) for e in params]

        # load function dynamically
        p,m = func.rsplit('.',1)
        mod = import_module(p)
        met = getattr(mod,m)

        return met(*params)

     else:
        for ch in chns:
            #find all potential variables 
            exprl = re.findall(r"[a-zA-Z_$][\w$]*",expr)

            # find fields in either dsp, hit, evt or parameters, prepare evaluation
            evt_dic = {}
            if os.path.exists(f_evt): evt_dic = store.load_nda(f_evt,[e.split('/')[-1] for e in store.ls(f_evt) if e.split('/')[-1] in exprl])
            hit_dic = store.load_nda(f_hit,[e.split('/')[-1] for e in store.ls(f_hit,ch+"/hit/") if e.split('/')[-1] in exprl],ch+"/hit/")
            dsp_dic = store.load_nda(f_dsp,[e.split('/')[-1] for e in store.ls(f_dsp,ch+"/dsp/") if e.split('/')[-1] in exprl],ch+"/dsp/")
            
            var= hit_dic | dsp_dic | evt_dic
            if para: var = var | para

            # evaluate expression
            res = eval(expr,var)
            if not isinstance(res, np.ndarray):
                res = np.full(len(out),res,dtype=type(res))

            # append to out according to mode
            ops = re.findall(r'([<>]=?|==)', mode)
            if len(ops)>0:
                op = ops[0]
                lim = float(mode.split(op)[-1])
                limarr = eval("res"+op+"lim",{"res":res,"lim":lim})
            else:
                
                limarr = np.ones(len(res)).astype(bool)
            if "first" in mode:
                outt = np.zeros(len(out))
                outt[:] = np.inf
                t0 = store.load_nda(f_dsp,["tp_0_est"],ch+"/dsp/")["tp_0_est"]
                out = np.where((t0<outt) & (limarr),res,out)
                out_chs = np.where((t0<outt) & (limarr),int(ch[2:]),out_chs)
                outt = np.where((t0<outt) & (limarr),t0,outt)
            elif "last" in mode:
                outt = np.zeros(len(out))
                t0 = store.load_nda(f_dsp,["tp_0_est"],ch+"/dsp/")["tp_0_est"]
                out = np.where((t0>outt) & (limarr),res,out)
                out_chs = np.where((t0<outt) & (limarr),int(ch[2:]),out_chs)
                outt = np.where((t0>outt) & (limarr),t0,outt)
            elif "tot" in mode:
                if ch == chns[0]: out[:] = 0
                if res.dtype == bool: res = res.astype(int)
                out += np.where(limarr,res,out)
            elif mode == "any":
                    if ch == chns[0]:
                        out = out.astype(bool)
                    if res.dtype != bool: res = res.astype(bool)
                    out = out | res
            elif mode == "all":
                    if ch == chns[0]: 
                        out = out.astype(bool)
                    if res.dtype != bool: res = res.astype(bool)
                    out = out & res
            elif mode in store.ls(f_evt):
                ch_comp = store.load_nda(f_evt,[mode])[mode]
                out = np.where(int(ch[2:]) == ch_comp,res,out)
            else:
                raise ValueError(mode + " not a valid mode")

     if getch: return out, out_chs
     else: return out       
             
def build_evt(
       f_dsp: str,
       f_hit: str,
       f_evt: str,
       meta_path: str = None,
       evt_config: str | dict = None,
       wo_mode: str = "write_safe"
) -> None:
    """
    Transform data from the hit and dsp levels which a channel sorted
    to a event sorted data format

    Parameters
    ----------
    f_dsp
        input LH5 file of the dsp level
    f_hit
        input LH5 file of the hit level
    f_evt
        name of the output file
    evt_config
        dictionary or name of JSON file defining evt fields. Channel lists can be defined by the user or by using the keyword "meta" followed by the system (geds/spms) and the usability (on,no_psd,ac,off) seperated by underscores (e.g. "meta_geds_on") in the "channels" dictonary. The "operations" dictionary defines the fields (name=key), where "channels" specifies the channels used to for this field (either a string or a list of strings), "mode" defines how the channels should be combined (see evaluate_expression). For first/last modes a "get_ch" flag can be defined, if true an additional field with the sufix "_id" is returned containing the rawid of the respective value in the field without the suffix. "expression" defnies the mathematical/special function to apply (see evaluate_expression), "parameters" defines any other parameter used in expression  For example:
        
        .. code-block::json

        {
            "channels": {
                "geds_on": "meta_geds_on",
                "geds_no_psd": "meta_geds_no_psd",
                "geds_ac": "meta_geds_ac",
                "spms_on": "meta_spms_on",
                "pulser": "PULS01",
                "baseline": "BSLN01",
                "muon": "MUON01",
                "ts_master":"S060"
            },
            "operations": {
                "energy":{
                    "channels": ["geds_on","geds_no_psd","geds_ac"],
                    "mode": "first>25",
                    "get_ch": true,
                    "expression": "cuspEmax_ctc_cal"
                },
                "aoe":{
                    "channels": ["geds_on"],
                    "mode": "energy_id",
                    "expression": "AoE_Classifier"
                },
                "is_muon_tagged":{
                    "channels": "muon",
                    "mode": "any",
                    "expression": "wf_max>a",
                    "parameters": {"a":15100}
                },
                "multiplicity":{
                    "channels":  ["geds_on","geds_no_psd","geds_ac"],
                    "mode": "tot",
                    "expression": "cuspEmax_ctc_cal > a",
                    "parameters": {"a":25}
                },
                "lar_energy":{
                    "channels": "spms_on",
                    "mode": "func",
                    "expression": "modules.spm.get_energy(0.5,t0,48000,1000,5000)"
                }
            }
        }
    """
    lstore = store.LH5Store()
    tbl_cfg = evt_config
    if isinstance(tbl_cfg,str):
        with open(tbl_cfg) as f:
                tbl_cfg = json.load(f)
    
    # create channel list according to config
    # This can be either read from the meta data 
    # or a list of channel names
    log.debug("Creating channel dictionary")
    if meta_path: lmeta = LegendMetadata(path=meta_path)
    else: lmeta = LegendMetadata()
    chmap = lmeta.channelmap(re.search("\d{8}T\d{6}Z",f_dsp).group(0))
    chns = {}
    for k, v in tbl_cfg['channels'].items():
         if isinstance(v,str):
            if "meta" in v:
                m,sys,usa = v.split("_",2)
                tmp = [f"ch{e}" for e in chmap.map("daq.rawid") if chmap.map("daq.rawid")[e]['system'] == sys]
                chns[k] = [e for e in tmp if chmap.map("daq.rawid")[int(e[2:])]['analysis']['usability'] == usa]
            else:
                 chns[k] = [f"ch{chmap.map('name')[v]['daq']['rawid']}"]
         elif isinstance(v,list):
              chns[k] = [f"ch{chmap.map('name')[e]['daq']['rawid']}" for e in v]
        

    # do operations
    first_iter = True
    log.info(f"Applying'{len(tbl_cfg['operations'].keys())} operations' to dsp file {f_dsp} and hit file {f_hit} to create evt file {f_evt}")
    for k, v in tbl_cfg['operations'].items(): 
         log.debug("Processing field" + k)

         # if channels not defined in operation, it can only be an operation on the evt level.
         if 'channels' not in v.keys():
            exprl = re.findall(r"[a-zA-Z_$][\w$]*",v["expression"])
            var = {}
            if os.path.exists(f_evt):var = store.load_nda(f_evt,[e.split('/')[-1] for e in store.ls(f_evt) if e.split('/')[-1] in exprl])
            if "parameters" in v.keys(): var = var | v['parameters']
            res = Array(eval(v["expression"],var))
            lstore.write_object(
                obj=res,
                name= k,
                lh5_file=f_evt,
                wo_mode=wo_mode #if first_iter else "append"
            )
            continue
             
         if isinstance(v['channels'],str): chns_e = chns[v['channels']]
         elif isinstance(v['channels'],list): chns_e = list(itertools.chain.from_iterable( [chns[e] for e in v['channels']]))

         pars = None
         defaultv = np.nan
         if "parameters" in v.keys(): pars = v['parameters']
         if "initial" in v.keys() and not v['initial'] == "np.nan" : defaultv = v['initial']

         if "get_ch" in v.keys():
              if "first" in v['mode'] or "last" in v['mode']:
                   res, chs = evaluate_expression(f_evt,f_hit,f_dsp,chns_e,v['mode'],v['expression'],pars,defaultv, v["get_ch"])
                   lstore.write_object(
                        obj=Array(res),
                        name= k,
                        lh5_file=f_evt,
                        wo_mode=wo_mode #if first_iter else "append"
                   )
                   lstore.write_object(
                        obj=Array(chs),
                        name= k+"_id",
                        lh5_file=f_evt,
                        wo_mode=wo_mode #if first_iter else "append"
                   )
                   
              else:
                  raise ValueError("get_ch can be only applied to first and last modes")
                
         else:
            res = Array(evaluate_expression(f_evt,f_hit,f_dsp,chns_e,v['mode'],v['expression'],pars,defaultv))

            lstore.write_object(
                obj=res,
                name= k,
                lh5_file=f_evt,
                wo_mode=wo_mode #if first_iter else "append"
            )
         if first_iter: first_iter = False

    log.info("Done")