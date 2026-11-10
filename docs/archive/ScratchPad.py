#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Feb 22 10:21:00 2025

@author: woody_old
"""



import sys
import numpy as np
import random
import math
import tkinter as tk
from tkinter import ttk
import pathlib

from functools import partial
#from tkinter import *
from tkinter import ttk
from PIL import ImageTk
from PIL import Image
############################################################################################################3

root = tk.Tk()
root.title('Full Window Scrolling X Y Scrollbar Example')
root.geometry("1350x400")
# Create A Main frame
main_frame = tk.Frame(root)
main_frame.pack(fill=tk.BOTH,expand=1)
# Create Frame for X Scrollbar
sec = tk.Frame(main_frame)
sec.pack(fill=tk.X,side=tk.BOTTOM)
# Create A Canvas
my_canvas = tk.Canvas(main_frame)
my_canvas.pack(side=tk.LEFT,fill=tk.BOTH,expand=1)
# Add A Scrollbars to Canvas
x_scrollbar = ttk.Scrollbar(sec,orient=tk.HORIZONTAL,command=my_canvas.xview)
x_scrollbar.pack(side=tk.BOTTOM,fill=tk.X)
y_scrollbar = ttk.Scrollbar(main_frame,orient=tk.VERTICAL,command=my_canvas.yview)
y_scrollbar.pack(side=tk.RIGHT,fill=tk.Y)
# Configure the canvas
my_canvas.configure(xscrollcommand=x_scrollbar.set)
my_canvas.configure(yscrollcommand=y_scrollbar.set)
my_canvas.bind("<Configure>",lambda e: my_canvas.config(scrollregion= my_canvas.bbox(tk.ALL)))
# Create Another Frame INSIDE the Canvas
second_frame = tk.Frame(my_canvas)
# Add that New Frame a Window In The Canvas
my_canvas.create_window((0,0),window= second_frame, anchor="nw")


for thing in range(100):
    tk.Button(second_frame ,text=f"Button  {thing}").grid(row=5,column=thing,pady=10,padx=10)
for thing in range(100):
    tk.Button(second_frame ,text=f"Button  {thing}").grid(row=thing,column=5,pady=10,padx=10)


root.mainloop()
