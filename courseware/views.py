from django.http import HttpResponse
from django.template import Context, loader
from djangomako.shortcuts import render_to_response, render_to_string
from xml.dom.minidom import parse, parseString
import json, os, sys
from django.core.context_processors import csrf

from django.template import Context
from django.contrib.auth.models import User
from auth.models import UserProfile
from django.shortcuts import redirect

import StringIO

from django.http import Http404

import urllib

import capa_module
import video_module

from models import StudentModule

import urllib

from django.conf import settings

import content_parser

template_imports={'urllib':urllib}

def profile(request):
    ''' User profile. Show username, location, etc, as well as grades .
        We need to allow the user to change some of these settings .'''
    if not request.user.is_authenticated():
        return redirect('/')

    dom=parse(settings.DATA_DIR+'course.xml')
    hw=[]
    course = dom.getElementsByTagName('course')[0]
    chapters = course.getElementsByTagName('chapter')

    responses=StudentModule.objects.filter(student=request.user)

    for c in chapters:
        for s in c.getElementsByTagName('section'):
            problems=s.getElementsByTagName('problem')
            scores=[]
            if len(problems)>0:
                for p in problems:
                    id = p.getAttribute('filename')
                    correct = 0
                    for response in responses:
                        if response.module_id == id:
                            if response.grade!=None:
                                correct=response.grade
                            else:
                                correct=0
                    total=capa_module.LoncapaModule(p.toxml(), "id").max_score() # TODO: Add state. Not useful now, but maybe someday problems will have randomized max scores? 
                    scores.append((int(correct),total))
                score={'course':course.getAttribute('name'),
                       'section':s.getAttribute("name"),
                       'chapter':c.getAttribute("name"),
                       'scores':scores,
                       }
                hw.append(score)

    user_info=UserProfile.objects.get(user=request.user)

    context={'name':user_info.name,
             'username':request.user.username,
             'location':user_info.location,
             'language':user_info.language,
             'email':request.user.email,
             'homeworks':hw, 
             'csrf':csrf(request)['csrf_token']
             }
    return render_to_response('profile.html', context)

def render_accordion(request,course,chapter,section):
    ''' Draws navigation bar. Takes current position in accordion as
        parameter. Returns (initialization_javascript, content)'''
    def format_string(string):
        return urllib.quote(string.replace(' ','_'))

    toc=content_parser.toc_from_xml(chapter, section)
    active_chapter=1
    for i in range(len(toc)):
        if toc[i]['active']:
            active_chapter=i
    context=dict([['active_chapter',active_chapter],
                  ['toc',toc], 
                  ['course_name',course],
                  ['format_string',format_string]]+ \
                     template_imports.items())
    return {'js':render_to_string('accordion_init.js',context), 
            'content':render_to_string('accordion.html',context)}

def video_mod(request, module):
    ''' Shows a video, with subtitles. 
    '''
    id=module.getAttribute('youtube')
    return {'js':render_to_string('video_init.js',{'id':id}), 
            'content':render_to_string('video.html',{'id':id})}

def html_module(request, module):
    ''' Show basic text
    '''
    template_source=module.getAttribute('filename')
    return {'content':render_to_string(template_source, {})}

def tab_module(request, module):
    ''' Layout module which lays out content in tabs.  
    '''
    contents=[(e.getAttribute("name"),render_module(request, e)) \
              for e in module.childNodes \
              if e.nodeType==1]
    js="".join([e[1]['js'] for e in contents if 'js' in e[1]])

    return {'js':render_to_string('tab_module.js',{'tabs':contents})+js, 
            'content':render_to_string('tab_module.html',{'tabs':contents})}

def vertical_module(request, module):
    ''' Layout module which lays out content vertically. 
    '''
    contents=[(e.getAttribute("name"),render_module(request, e)) \
              for e in module.childNodes \
              if e.nodeType==1]
    js="".join([e[1]['js'] for e in contents if 'js' in e[1]])

    return {'js':js, 
            'content':render_to_string('vert_module.html',{'items':contents})}

def seq_module(request, module):
    ''' Layout module which lays out content in a temporal sequence
    '''
    def j(m): 
        # jsonify contents so it can be embedded in a js array
        # We also need to split </script> tags so they don't break
        # mid-string
        if 'js' not in m: m['js']=""
        content=json.dumps(m['content']) 
        content=content.replace('</script>', '<"+"/script>') 
        return {'content':content, 'js':m['js']}
    contents=[(e.getAttribute("name"),j(render_module(request, e))) \
              for e in module.childNodes \
              if e.nodeType==1]
        
    js="".join([e[1]['js'] for e in contents if 'js' in e[1]])

    return {'js':js+render_to_string('seq_module.js',{'items':contents}), 
            'content':render_to_string('seq_module.html',{'items':contents})}


modx_modules={'problem':capa_module.LoncapaModule}#, 'video1':video_module.VideoModule}

def render_x_module(request, xml_module):
    ''' Generic module for extensions. This renders to HTML. '''
    # Check if problem has an instance in DB
    module_type=xml_module.nodeName
    module_class=modx_modules[module_type]
    module_id=xml_module.getAttribute(module_class.id_attribute)
    s = StudentModule.objects.filter(student=request.user, module_id=module_id)
    if len(s) == 0:
        # If not, create one, and save it
        instance=module_class(xml_module.toxml(), module_id)
        smod=StudentModule(student=request.user, 
                           module_id=module_id, 
                           state=instance.get_state(), 
                           xml=instance.xml)
        smod.save()
    elif len(s) == 1:
        # If so, render it
        s=s[0]
        instance=module_class(xml_module.toxml(), 
                             module_id, 
                             state=s.state)
        s.state=instance.get_state()
        s.save()
    else:
        raise Exception("Database is inconsistent (1).")

    return {'content':instance.get_html(), 
            'js':instance.get_js()}

def modx_dispatch(request, module=None, dispatch=None, id=None):
    ''' Generic module for extensions. This handles AJAX. '''
    s = StudentModule.objects.filter(module_type=module, student=request.user, module_id=id)
    if len(s) == 0:
        raise Http404

    s=s[0]

    dispatch=dispatch.split('?')[0]
    instance=modx_modules[module](s.xml, s.module_id, state=s.state)
    html=instance.handle_ajax(dispatch, request.GET)
    s.state=instance.get_state()
    s.grade=instance.get_score()['score']
    s.save()
    return HttpResponse(html)

module_types={'video':video_mod,
              'html':html_module,
              'tab':tab_module,
              'vertical':vertical_module,
              'sequential':seq_module,
              'problem':render_x_module}
                  #'lab':lab_module,

def render_module(request, module):
    ''' Generic dispatch for internal modules. '''
    if module==None:
        return {"content":""}
    if str(module.localName) in module_types:
        return module_types[module.localName](request, module)
    return {"content":""}

def index(request, course="6.002 Spring 2012", chapter="Using the System", section="Hints"): 
    ''' Displays courseware accordion, and any associated content. 
    ''' 
    if not request.user.is_authenticated():
        return redirect('/')

    # Fix URLs
    course=course.replace("_"," ")
    chapter=chapter.replace("_"," ")
    section=section.replace("_"," ")

    # HACK: Force course to 6.002 for now
    # Without this, URLs break
    if course!="6.002 Spring 2012":
        return redirect('/')

    dom=parse(settings.DATA_DIR+'course.xml')
    dom_course=content_parser.dom_select(dom, 'course', course)
    dom_chapter=content_parser.dom_select(dom_course, 'chapter', chapter)
    dom_section=content_parser.dom_select(dom_chapter, 'section', section)
    if dom_section!=None:
        module=[e for e in dom_section.childNodes if e.nodeType==1][0]
    else:
        module=None

    accordion=render_accordion(request, course, chapter, section)

    module=render_module(request, module)

    if 'js' not in module:
        module['js']=''

    context={'init':accordion['js']+module['js'],
             'accordion':accordion['content'],
             'content':module['content']}
    return render_to_response('courseware.html', context)


